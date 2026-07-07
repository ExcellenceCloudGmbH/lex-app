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

### Batch 1t — `DISABLE_SERVER_SIDE_CURSORS` placement (production cursor crash) ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.169 – 1.170 |
| Type | U |
| Files covered | `lex/lex_app/settings.py` (the flag was declared at module level, where Django ignores it; moved into each PostgreSQL `DATABASES` alias config dict — the only place `connection.settings_dict` reads it — so server-side cursors are actually disabled behind the `cloud-sql-proxy`/pgbouncer transaction-pooling proxy that otherwise causes `InvalidCursorName` on every `.iterator()`) |
| Test file | `lex/test_project/tests/init/test_1t_disable_server_side_cursors.py` |
| Test classes | `TestCluster01t_DisableServerSideCursors` (1.169 every Postgres alias carries the flag in its config dict, 1.170 the live `connections["default"].settings_dict` honours it on Postgres / engine-gated for SQLite) |
| Fixtures | none (introspects `settings.DATABASES` + `django.db.connections`) |
| Tests landed | **2 pass / 0 fail in 0.09s** |
| Coverage gain | negligible (settings is import-time; pins a config-placement contract) |
| Status | ✅ Complete (Session 78 — June 9) |

### Batch 1u — Fast ASGI health/readiness probes (coverage task #620) ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.171 – 1.175 |
| Type | U |
| Files covered | `lex/lex_app/fast_health.py`, `lex/lex_app/asgi.py` |
| Test file | `lex/test_project/tests/init/test_1u_fast_health_asgi.py` |
| Test classes | `TestCluster01u_FastHealthAsgi` (1.171 path helpers separate liveness/readiness, 1.172 health app drains request body and returns static Healthy payload, 1.173 readiness returns 200/503 based on DB readiness seam, 1.174 top-level HTTP ASGI app short-circuits probe paths before Django, 1.175 non-probe HTTP delegates to Django) |
| Fixtures | none — ASGI `receive`/`send` callables and `AsyncMock` seams only |
| Tests landed | **5 pass / 0 fail** (direct pytest) |
| Coverage gain | `fast_health.py` path helpers + health/readiness ASGI apps; `asgi.py` `http_application` health/readiness/Django routing branches |
| Status | ✅ Complete (Session 81 — June 18). `python -m lex pytest ...` blocked locally by no PostgreSQL service; pure U tests pass with `DJANGO_SETTINGS_MODULE=lex_app.settings python -m pytest ...`. |

---

### Batch 1v — `TIME_ZONE`↔`USE_TZ` coupling for `django_celery_beat` DatabaseScheduler ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.179 – 1.183 |
| Type | U |
| Files covered | `lex/lex_app/settings.py` (USE_TZ↔TIME_ZONE coupling); guards the `django_celery_beat` `is_due` path |
| Test file | `lex/test_project/tests/init/test_1v_scheduler_tz_invariant.py` |
| Test classes | `TestCluster01v_TimezoneInvariant` (1.179 `USE_TZ=False ⟹ TIME_ZONE=="UTC"`, 1.180 `timezone.now()` naive frame within seconds of real UTC, 1.181 recovery `IntervalSchedule` due in live frame via `ModelEntry.is_due` replica, 1.182 future-edit `clocked(now+30s)` ~30s away not hours + past due, 1.183 naive-UTC round-trips exact vs naive-Berlin misread ≥3600s) |
| Fixtures | none — `celery.schedules.schedule` / `django_celery_beat.clockedschedule.clocked` against `lex.lex_app.celery.app` |
| Tests landed | **5 pass / 0 fail** (direct pytest) |
| Coverage gain | settings-level `TIME_ZONE` coupling under `USE_TZ=False`; pins the `maybe_make_aware` naive-as-UTC read for both the recovery interval sweep and future-edit clocked schedule |
| Status | ✅ Complete (Session 84 — June 26). Regression: history+init+settings 63 pass / 1 skip; celery_async+audit_logging 262 pass / 4 skip / 1 xfail. |

---

### Batch 1w — `LEX_TASK_RECOVERY_ENABLED` defaults OFF (stuck calc resets on restart) ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.184 – 1.186 |
| Type | U |
| Files covered | `lex/lex_app/settings.py` (`LEX_TASK_RECOVERY_ENABLED` default flipped `true` → `false`) |
| Test file | `lex/test_project/tests/init/test_1w_recovery_default_deployment_target.py` |
| Test classes | `TestCluster01w_RecoveryDefaultOff` (1.184 env unset ⟹ `False`; 1.185 explicit `=true` ⟹ `True` opt-in; 1.186 explicit `=false` ⟹ `False` + case-insensitive `TRUE` ⟹ `True`) |
| Fixtures | none — env-patch + `importlib.reload(lex.lex_app.settings)` harness (mirrors 1s), `sentry_sdk.init` mocked per reload |
| Tests landed | **3 pass / 0 fail** (direct pytest) |
| Coverage gain | settings-level recovery master-switch default resolution |
| Status | ✅ Complete (Session 90 — July 1). Default OFF keeps the startup sweep in blind-abort mode so a stuck `IN_PROGRESS` row is reset on restart when no recovery-supervisor pod runs (local/CI/un-provisioned deploys); prod opts back in explicitly. Verified nested-dispatch untouched: 7j/7q/8ab all pass. Pre-existing unrelated `test_15d` logging-chain failures reproduce identically with the old `=true` default. |

### Batch 1u — `setup-with-ai` MCP mode parity ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.171 – 1.175 |
| Type | U |
| Files covered | `lex/tools/setup_with_ai.py`, `lex/tools/ai_dashboard.py` (shared mode registry + setup form rendering/validation + unified/legacy MCP arg forwarding) |
| Test file | `lex/test_project/tests/init/test_1u_setup_with_ai_mcp_modes.py` |
| Test classes | `TestCluster01u_SetupWithAiMcpModes` (1.171 supported-mode round-trip, 1.172 safe fallback, 1.173 unified `--mode` args, 1.174 legacy wrapper positional mode, 1.175 full setup-form mode-card rendering) |
| Fixtures | none (pure-Python; `_has_unified_mcp_entry_point` / `resolve_wrapper_script_path` patched at import site) |
| Tests landed | **5 pass / 0 fail in 0.26s** |
| Coverage gain | modest but high-value; pins the first-touch MCP mode contract across setup + dashboard |
| Status | ✅ Complete (Session 80 — July 1) |

### Batch 1v — `ai-faq` hosted-page launcher ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.176 – 1.178 |
| Type | U |
| Files covered | `lex/tools/ai_faq.py` (`launch_ai_faq` now opens the hosted FAQ URL directly; no in-process localhost HTTP server) |
| Test file | `lex/test_project/tests/init/test_1v_ai_faq_command.py` |
| Test classes | `TestCluster01v_AiFaqHostedUrlLauncher` (1.176 default hosted URL, 1.177 `LEX_AI_FAQ_URL` override, 1.178 manual fallback message when browser auto-open fails) |
| Fixtures | none (`webbrowser.open` + environment patched) |
| Tests landed | **3 pass / 0 fail** |
| Coverage gain | small but high-value; pins operator-visible FAQ launch target |
| Status | ✅ Complete (Session 81 — July 5) |

### Batch 1w — `ai_issue_report` raw artifact bundle ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.179 – 1.181 |
| Type | U |
| Files covered | `lex/bin/lex.py`, `lex/tools/ai_issue_report.py` (new `ai_issue_report` command + raw Copilot/MCP artifact zip export, no pre-parse normalization) |
| Test file | `lex/test_project/tests/init/test_1w_ai_issue_report.py` |
| Test classes | `TestCluster01w_AiIssueReportRawArtifacts` (1.179 off-mode shell report, 1.180 raw byte-preserving capture + inventory, 1.181 strict-mode no-artifact failure gate) |
| Fixtures | none (`TemporaryDirectory`, synthetic artifact files, helper patching) |
| Tests landed | **3 pass / 0 fail** |
| Coverage gain | modest but high-value; pins support-visible raw bundle contract and strict-mode guard |
| Status | ✅ Complete (Session 82 — July 5) |

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

### Batch 2j — Instance API-key extraction and matching (Session 80 — June 18)

| Property | Value |
| --- | --- |
| Scenario range | 2.97 – 2.107 |
| Type | U |
| Files covered | `lex/api/utils/api_key_requests.py` (`get_raw_api_key`, `is_instance_api_key_request`) |
| Test file | `lex/test_project/tests/crud_api/test_2j_instance_api_key.py` |
| Test classes | `TestCluster02j_GetRawApiKey` (2.97–2.103 — KeyParser hit, header fallback, prefix strip, empty candidate, no source, DRF wrapped request, non-ApiKey header), `TestCluster02j_IsInstanceApiKeyRequest` (2.104–2.107 — match, mismatch, no env var, no key in request) |
| Fixtures | none — `SimpleTestCase` with `patch` on `KeyParser` and `patch.dict("os.environ")` |
| Tests landed | 11 pass / 0 fail |
| Coverage gain | `lex/api/utils/api_key_requests.py` `get_raw_api_key` + `is_instance_api_key_request` branches |
| Status | ✅ Complete (Session 80 — June 18) |

---

## Cluster 3 — Validation Hooks (existing 3a–3d)

### Batch 3e — Pre-validation snapshot lifecycle (v1→v2 calculate-all memory fix) ✅

| Property | Value |
| --- | --- |
| Scenario range | 3.9 – 3.10 |
| Type | E (E2E save through the public `save()` entry point) |
| Files covered | `lex/core/models/LexModel.py` (`post_validation_hook` — releases `_pre_validation_snapshot` on the successful path) |
| Test file | `lex/test_project/tests/validation_hooks/test_3e_snapshot_lifecycle.py` |
| Test classes | `TestCluster03e_SnapshotLifecycle` (3.9 snapshot released after a successful create *and* update, 3.10 release-on-success does not weaken rollback — a later rejected update still restores the pre-save value) |
| Fixtures | existing `PostValidatedItem` (reused, no new model) |
| Tests landed | 2 pass / 0 fail (full cluster 3: 11 pass / 0 fail) |
| Coverage gain | successful-path snapshot release in `post_validation_hook` |
| Status | ✅ Complete (Session 82 — June 22) |
| Note | The `_pre_validation_snapshot` is an in-flight rollback buffer (a second full-field copy per row); it was never freed after a successful save, pinning ~1800 B/inst (~34% of the v2 per-instance footprint) for the instance's lifetime — the measured driver of the non-atomic `calculate_all` v1→v2 RAM regression (3.28× → ~2.17× per saved row). |

---

### Batch 3f — Default-on lean `_initial_state` (last full-field snapshot removed; hooks preserved) ✅

| Property | Value |
| --- | --- |
| Scenario range | 3.11 – 3.32 |
| Type | E (E2E through `save()` / `refresh_from_db`; on-commit re-baseline needs `TransactionTestCase`) |
| Files covered | `lex/core/models/LexModel.py` (`lex_lean_initial_state` flag, `lex_initial_state_extra_fields`, `_expand_field_ref`, `_field_names_from_condition`, `_fields_from_hook_config`, `_lean_tracked_field_names`, `_build_lean_initial_state`, `_reset_initial_state` override, `refresh_from_db` override, `__init__` hook) |
| Test file | `lex/test_project/tests/validation_hooks/test_3f_lean_initial_state.py` |
| Test classes | `TestCluster03f_LeanInitialState` — 3.11 default-on + explicit opt-out keeps full snapshot; 3.12–3.13 lean snapshot shape (tracked-only); 3.14–3.15 `edited_at` auto-stamp + explicit override; 3.16–3.23 every conditional form (legacy `when=`/`when_any=`, `WhenFieldHasChanged`/`WhenFieldValueWas`/`WhenFieldValueChangesTo`, chained); 3.24 lean-vs-full parity; 3.25–3.27 `has_changed`/`initial_value`; 3.28 escape hatch; 3.29 post-save re-baseline; 3.30 `refresh_from_db` re-baseline; 3.31 create-path stamping; 3.32 snapshot strictly smaller |
| Fixtures | `_ConditionalHooksBase` (abstract) → `LeanConditionalItem` (lean, matches the new default) / `FullConditionalItem` (explicit `lex_lean_initial_state=False` opt-out, control) + `LeanExtraFieldItem` (escape hatch) — added to `validation_hooks/models.py` |
| Tests landed | 22 pass / 0 fail (full cluster 3: 33 pass / 0 fail) |
| Coverage gain | the new lean-snapshot machinery on `LexModel` |
| Status | ✅ Complete (Session 83 — June 23) |
| Note | django-lifecycle's `_initial_state` is a second full-field copy per instance (set in `__init__`, re-captured after each save) — the ~2.17× per-row floor left after 3e. The framework's only dependency is `has_changed('edited_at')`; all other consumers are statically-discoverable hook clauses. The opt-in narrows the retained snapshot to `edited_at` + hook-clause fields + declared extras, built by filtering the full snapshot so tracked values stay byte-for-byte identical. Default **on** framework-wide — the narrowing is transparent because every consumer is either `has_changed('edited_at')` or a statically-discoverable hook clause; a model that queries `has_changed`/`initial_value` on an undeclared field lists it in `lex_initial_state_extra_fields` or sets `lex_lean_initial_state = False`. |

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

### Batch 4m — `ApiKeyAwareLoginRequiredMiddleware` instance-key bypass (Session 80 — June 18)

| Property | Value |
| --- | --- |
| Scenario range | 4.66 – 4.70 |
| Type | U |
| Files covered | `lex/authentication/middleware.py` (`ApiKeyAwareLoginRequiredMiddleware.check_login_required`) |
| Test file | `lex/test_project/tests/permissions/test_4m_api_key_middleware.py` |
| Test classes | `TestCluster04m_ApiKeyAwareMiddleware` (4.66–4.70 — instance key bypass, DRF key bypass, non-key delegates to parent, instance check still evaluated when DRF check false, subclass contract) |
| Fixtures | none — `SimpleTestCase` with `patch` on `is_instance_api_key_request`, `is_api_key_request`, and parent `check_login_required` |
| Tests landed | 5 pass / 0 fail |
| Coverage gain | `lex/authentication/middleware.py` new `is_instance_api_key_request` branch |
| Status | ✅ Complete (Session 80 — June 18) |

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

### Batch 7p — Sync-mode streaming combinatorial expansion (OOM fix) ✅

| Property | Value |
| --- | --- |
| Scenario range | 7.178 – 7.187 |
| Type | U (generator + flag, pure logic) + I (E2E sync create saved rows) |
| Files covered | `lex/core/mixins/CalculatedModelMixin.py` (`generate_model_combinations_streaming`, `calc_and_save_streaming`, `_sync_streaming_enabled`, `create()` mode branch) |
| Test file | `lex/test_project/tests/calculations/test_7p_streaming_expansion.py` |
| Test classes | `TestCluster07p_StreamingEquivalence` (7.178–7.182), `TestCluster07p_StreamingFlag` (7.183–7.185), `TestCluster07p_SyncCreateEquivalence` (7.186), `TestCluster07p_StreamingMemoryBound` (7.187) |
| Fixtures | `_FakeCalcModel` (pure logic); existing `CombinatorialCalc` E2E model (reused, no new model) |
| Tests landed | 10 pass / 0 fail |
| Coverage gain | streaming generator + consumer + flag + create() sync branch |
| Status | ✅ Complete (Session 79 — June 10) |
| Note | Backend OOM fix: sync-mode expansion now streams (O(depth)) instead of materializing all N. `LEX_SYNC_STREAMING_EXPANSION=false` valve. Benchmark in `docs/runs/`. |


---

### Batch 7m — `CalculationSignals` + `One.py` `model_name` propagation (Session 80 — June 18)

| Property | Value |
| --- | --- |
| Scenario range | 7.188 – 7.195 |
| Type | U + E |
| Files covered | `lex/core/signals/CalculationSignals.py` (`update_calculation_status` IN_PROGRESS branch), `lex/api/views/model_entries/One.py` (early-registration `mark_in_progress` call) |
| Test file | `lex/test_project/tests/calculations/test_7m_calc_signals.py` |
| Test classes | `TestCluster07m_SignalModelName` (7.188–7.191 — IN_PROGRESS passes object_name, SUCCESS/ERROR do not call mark_in_progress, non-calculation model returns early), `TestCluster07m_OneModelNamePropagation` (7.192–7.193 — calculate=true passes model_name to store, record_id matches) |
| Fixtures | `_FakeInstance` / `_FakeSignal` (pure unit); `AtomicCalc` (reused from cluster 7, via E2ETestCase) |
| Tests landed | 8 pass / 0 fail |
| Coverage gain | `CalculationSignals.py` IN_PROGRESS `model_name` kwarg path; `One.py` early-registration `mark_in_progress(model_name=…)` branch |
| Status | ✅ Complete (Session 80 — June 18) |

---

### Batch 7q — Nested fan-out dispatches by default from inside a worker (Session 86 — June 29)

| Property | Value |
| --- | --- |
| Scenario range | 7.196 – 7.201 |
| Type | E |
| Files covered | `lex/core/mixins/CalculatedModelMixin.py` (`_dispatch_model_processing` — removed the `is_celery_worker_process()` inline-inside-worker guard; now fans out to `CeleryTaskDispatcher` whenever `CELERY_ACTIVE` + `cls.calculate.delay` exist) and `lex/core/models/CalculationModel.py` (`calculate_hook` dispatch branch — removed the `elif is_celery_worker_process(): execute_calculation_sync()` branch; now always dispatches, reusing an outer async context or opening its own `WaitForTasks` + blocking on the child result). Driven by a production bug: `InvestmentPosting` (65 568 models, 50 clusters) ran synchronously on one worker slot because the guard made nested fan-out opt-in |
| Test file | `lex/test_project/tests/calculations/test_7q_worker_default_dispatch.py` |
| Test classes | `TestCluster07q_MixinWorkerDefaultDispatch` (7.196 no-context / 7.197 WaitForTasks / 7.198 FireAndForget — all fan out to the dispatcher, never `calc_and_save_sync`); `TestCluster07q_CalculationModelWorkerDefaultDispatch` (7.199 no-context ⇒ own WaitForTasks dispatches + blocks; 7.200 outer WaitForTasks ⇒ child drained only on scope exit; 7.201 FireAndForget ⇒ dispatches, never blocks) |
| Fixtures | `CombinatorialCalc` / `AtomicCalc` (reused from cluster 7, via E2ETestCase); explicit async contexts entered inside the `_celery_is_active=True` patch via a `_worker_patches` helper-CM so they register on the contextvar stack |
| Tests landed | 6 pass / 0 fail. Companion stale-test updates flipped to the new default: `test_calculation_wait_contexts.py` (2 pass), `test_calculated_model_mixin.py` (dispatch tests pass; `test_create_treats_empty_selections_as_valid_noop` is a pre-existing streaming-path failure unrelated to this change — confirmed via git stash), `test_8b_dispatch_context.py` 8.6 message (2 pass) |
| Coverage gain | the default-dispatch branch of both `_dispatch_model_processing` and `calculate_hook` (previously only the explicit-context and inline-worker branches were exercised) |
| Status | ✅ Complete — source fix (2 files) + paired tests + stale-test fixes + plan sync in one change. Allocated `7q` (next free letter after `7p`); 7.196 picks up after cluster-7 scenario max 7.195. **Session 91 note:** Session 89 briefly flipped 7.199 to inline (draft Batch 7r); withdrawn before commit — always-dispatch is the pinned default on both paths, abort-safety via the cancel marker (Batch 8ad) |

---

### Batch 7r — (withdrawn — Session 91) Per-instance inline-inside-worker guard

| Property | Value |
| --- | --- |
| Scenario range | 7.202 – 7.204 (never landed — reusable by the next cluster-7 batch) |
| Status | ❌ **Withdrawn before commit** (Session 91 — July 2). The Session 89 draft restored an `is_celery_worker_process()` inline guard on the per-instance `CalculationModel.calculate_hook` path as the Report 1 (abort→resume) fix. Live verification showed it broke the core parallelism contract: a nested calc inside a worker (e.g. a project's `CalculateNAV` inside another calculation) ran INLINE on the parent's worker instead of dispatching to a free one, and only an explicit `WaitForTasks` restored dispatch. Per the developer's explicit requirement ("calculation can dispatch calculations"), the guard, the 7q 7.199 flip, and `test_7r_nested_worker_inline_abort_safe.py` were all withdrawn; always-dispatch (7q) is the pinned default on both paths. Report 1 abort-safety is provided by the restart-surviving cluster cancel marker instead — see **Batch 8ad**. Letter 7r stays reserved for this record |

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

### Batch 8y — Embedded-beat recovery driver: schedule wiring, queue isolation, entrypoint

| Property | Value |
| --- | --- |
| Scenario range | 8.116 – 8.122 |
| Type | U |
| Files covered | `lex/lex_app/settings.py` — the `CELERY_BEAT_SCHEDULE` `lex-celery-recovery-sweep` entry: `task` bound to the registered `sweep_dead_workers` name, `options.queue = "recovery"` (off the main KEDA-watched queue), `options.expires` bounding stale ticks. `lex/lex_app/celery_recovery/entrypoint.py` — new `beat_main(argv=None)` obtains the one canonical app via `supervisor._get_app()` and calls `app.worker_main(["worker","-B","-Q","recovery","--concurrency","1","--scheduler","django_celery_beat.schedulers:DatabaseScheduler","-l","info", …])`; registered as the `lex-recovery-beat` console script in `pyproject.toml`. `lex/lex_app/celery_recovery/supervisor.py` — `_requeue` queue routing (unchanged, pinned: `incremented.get("queue") or _default_queue()` ⇒ recovered task to its main queue, never `recovery`). `lex/lex_app/celery_recovery/heartbeat.py` — `_UNTRACKED_TASK_NAMES` already excludes the sweep (pinned). |
| Test file | `lex/test_project/tests/celery_async/test_8y_beat_recovery_driver.py` |
| Test classes | `TestCluster08y_BeatScheduleWiring` (8.116–8.119 — `SimpleTestCase`, reads `django.conf.settings` + `heartbeat._UNTRACKED_TASK_NAMES`: schedule names the registered sweep / sweep excluded from heartbeat tracking / sweep on dedicated `recovery` queue / that queue ≠ main default queue); `TestCluster08y_RequeueRoutingInvariant` (8.120–8.121 — fake Celery app records `send_task`: recovered task to payload's main queue not `recovery` / missing-queue fallback is the default main queue); `TestCluster08y_RecoveryBeatEntrypoint` (8.122 — `mock.patch` `worker_main` on `supervisor._get_app()`: argv starts `worker` with `-B`, binds `-Q recovery`, selects the `DatabaseScheduler`) |
| Fixtures | none — `unittest.mock` on `app.worker_main` and a synthetic fake Celery app (`send_task`/`backend.mark_as_failure`); settings read directly from `django.conf.settings`. Broker, Redis, and Celery itself never contacted |
| Tests landed | **7 pass / 0 fail** locally (0.09s) — pure-logic U across the settings dict, the heartbeat frozenset, `_requeue`'s queue selection, and the `worker_main` argv |
| Coverage gain | `lex/lex_app/celery_recovery/entrypoint.py` — `beat_main` + factored `_bootstrap_django` newly covered; `lex/lex_app/celery_recovery/supervisor.py` — `_requeue` main-queue routing pinned; `settings.py` `CELERY_BEAT_SCHEDULE` recovery entry asserted |
| Prereqs | none — all scenarios are broker-/DB-free. Infra (chart `celery_beat_recovery.yaml` + `workers.recoveryDriver` selector, supervisor gating) lives in `LEX_TERRAFORM_MODULES` on a matching branch and is out of scope for the framework test-plan |
| Status | ✅ Complete — source + paired cluster tests + plan sync in one change (tests were first mis-placed in the legacy `lex/tests/unit/infra/` audit tree; reverted and rewritten here per AGENTS.md Prime Directive 2) |

---

### Batch 8aa — Post-task warm shutdown honours the idle-shutdown master switch (Session 85 — June 26)

| Property | Value |
| --- | --- |
| Scenario range | 8.125 – 8.128 |
| Type | U |
| Files covered | `lex/lex_app/celery.py` — `shutdown_worker_after_task_completion` (the `task_postrun` handler). Added the missing `_idle_shutdown_enabled()` early-return guard so `LEX_WORKER_IDLE_SHUTDOWN_ENABLED=false` disables the post-task warm shutdown, matching the `task_revoked` fast-path and the `worker_ready` idle watchdog (which already gate on it). Fixes the embedded-beat recovery pod (`celery_beat_recovery.yaml`) crash-loop: it warm-shut-down after its first `sweep_dead_workers`, killing beat |
| Test file | `lex/test_project/tests/celery_async/test_8aa_postrun_shutdown_guard.py` |
| Test classes | `TestCluster08aa_PostrunShutdownGuard` (8.125 master switch off ⇒ no broadcast — the recovery-beat bug; 8.126 switch on + non-local ⇒ broadcast still fires to the completing worker with `completed_task_id` excluded; 8.127 local target ⇒ never broadcasts; 8.128 `task=None` ⇒ safe no-op) |
| Fixtures | none — `unittest.mock` on `_is_non_local_deployment_target` / `_idle_shutdown_enabled` and a task stand-in whose `.app.control.broadcast` is observed; broker-/DB-free |
| Tests landed | **4 pass / 0 fail** (8.125–8.128). Regression: full `celery_async` cluster + `lex/tests/unit/infra/test_worker_self_termination.py` = 144 pass / 4 skip / 12 subtests |
| Coverage gain | `lex/lex_app/celery.py` — the disabled-flag branch of `shutdown_worker_after_task_completion`, which previously had **no** test (the gap that let the bug ship) |
| Status | ✅ Complete — source fix + paired tests + plan sync in one change. Allocated `8aa` because `8y` (recovery driver) and `8z` (initial-data executor) were both already taken |

---

### Batch 8ab — Nested fan-out join is worker-safe: `allow_join_result` wrap (Session 88 — July 1)

| Property | Value |
| --- | --- |
| Scenario range | 8.129 – 8.138 |
| Type | U |
| Files covered | `lex/core/tasks/CeleryTaskDispatcher.py` — `_handle_task_results` now wraps `rs.join(propagate=False)` in `allow_join_result()` (import added to the lazy `from celery.result import ResultSet, allow_join_result`). A **nested** calc fanning out from inside a Celery worker (`dispatch_calculation_groups` → `_dispatch_single_group` → `_handle_task_results`) previously hit an unwrapped `ResultSet.join()`, which Celery hard-forbids inside a task ("Never call result.get() within a task!"). The old `is_celery_worker_process()` guard masked it by running nested calcs inline; removing that guard (batch 7q — nested fan-out now dispatches by default) exposed the crash. Prod repro: `InvestmentPosting` (5072 models, 2 tasks) crashed on the assertion, fell back to complete-sync, re-committed persisted rows → duplicate-key violation. Fix mirrors `WaitForTasks.wait_for_completion`, which already blocks under `allow_join_result` |
| Test file | `lex/test_project/tests/celery_async/test_8ab_dispatcher_join_worker_safe.py` |
| Test classes | `TestCluster08ab_HandleResultsJoinWorkerSafe` (8.129 in-worker join no longer raises — the exact crash regression; 8.130 worker join-block flag restored after return; 8.131 non-worker path unaffected; 8.132 worker-safe + failed group still retried; 8.133 worker-safe + join raises → complete sync; 8.134 worker-safe + raising status check still queues group; 8.135 `allow_join_result` import required); `TestCluster08ab_NestedDispatchWorkerSafe` (8.136 no context ⇒ implicit `WaitForTasks` + safe join — the production path; 8.137 explicit `WaitForTasks` reused + safe join; 8.138 explicit `FireAndForget` reused + safe join) |
| Fixtures | none — deterministic broker-free repro: `celery._state._set_task_join_will_block(True)` simulates a worker, a fake `ResultSet` (patched only on `celery.result.ResultSet`) whose `.join` calls the **real** `celery.result.assert_will_not_block()`, with the **real** `allow_join_result` left in place so the wrap is genuinely exercised. `calc_and_save.delay` / FF / WFT lazy imports mocked as in batch 8l |
| Tests landed | **10 pass / 0 fail** (8.129–8.138). Regression: full `celery_async` cluster + `test_7q_worker_default_dispatch.py` = 143 pass / 4 skip / 12 subtests |
| Coverage gain | `CeleryTaskDispatcher._handle_task_results` — the `allow_join_result`-wrapped join branch and its worker-context behaviour, plus the end-to-end nested `dispatch_calculation_groups` path under a simulated worker (previously only exercised outside a worker, where the missing wrap never triggered) |
| Status | ✅ Complete — source fix + paired tests + plan sync in one change. Allocated `8ab` (next free letter after `8aa`); 8.129 picks up after cluster-8 scenario max 8.128 |

---

### Batch 8ac — Complete-sync fallback is idempotent: Report 2 duplicate-key fix (Session 89 — July 1)

| Property | Value |
| --- | --- |
| Scenario range | 8.139 – 8.141 |
| Type | I (real DB — `CombinatorialCalc.defining_fields=[region, category]` yields a real `UniqueConstraint('defining_fields_CombinatorialCalc')`) |
| Files covered | `lex/core/mixins/CalculatedModelMixin.py` (`calc_and_save_sync` — now calls `delete_models_with_same_defining_fields()` immediately before `prepared.lex_func()(*args)` / `prepared.save()`, mirroring `calc_and_save_streaming`); exercises `lex/core/tasks/CeleryTaskDispatcher.py`'s complete-sync fallback. Production Report 2 (local, `celery --concurrency 3` + 14 workers): a connection storm (`FATAL: sorry, too many clients already`) crashed fan-out setup → the fallback ran `calc_and_save_sync(all_models)` on models dedup-resolved at T0 (pk reset for fresh INSERT) → a sibling had since committed the row → blind `save()` INSERTed a duplicate → `duplicate key value violates unique constraint "defining_fields_EndBalance"` (558 models). Re-resolving before save makes it idempotent: 1 existing row → UPDATE, 0 → INSERT. Option A (chosen over restoring the mixin inline guard) so 7q's parallel fan-out is preserved; one edit fixes all four fallback call sites (`CeleryTaskDispatcher.py:147, 229, 378, 412`) |
| Test file | `lex/test_project/tests/celery_async/test_8ac_sync_fallback_idempotent.py` |
| Test classes | `TestCluster08ac_SyncFallbackIdempotent` (8.139 fallback on a model whose defining-fields row a sibling already committed ⇒ re-resolves → UPDATE, same pk, exactly 1 row; 8.140 fallback on a fresh model ⇒ INSERT, 1 row; 8.141 end-to-end `dispatch_calculation_groups` with `_dispatch_single_group` raising a simulated connection storm + one row pre-committed ⇒ complete-sync fallback ⇒ NO duplicate key, (US,A) UPDATEd + (EU,B) INSERTed) |
| Fixtures | `CombinatorialCalc` (reused from cluster 7, via E2ETestCase — its defining-fields UNIQUE constraint is what makes the duplicate-key path real); `fail_for_region` reset to `None` in `setUp` |
| Tests landed | 3 pass / 0 fail (8.139–8.141). Regression: full `celery_async` cluster = 140 pass / 4 skip |
| Coverage gain | the re-resolve-before-save branch of `calc_and_save_sync` + the complete-sync fallback in `dispatch_calculation_groups`, now pinned as a live Report 2 duplicate-key regression gate |
| Status | ✅ Complete — source fix + paired tests + plan sync in one change. Allocated `8ac` (next free letter after `8ab`); 8.139 picks up after cluster-8 scenario max 8.138 |

---

### Batch 8ad — Dispatched @lex_shared_task self-aborts on the cluster cancel marker: Report 1 fix with dispatch preserved (Session 91 — July 2)

| Property | Value |
| --- | --- |
| Scenario range | 8.142 – 8.144 |
| Type | U (`SimpleTestCase` — pure wrapper logic; the cancel index is mocked at the module boundary) |
| Files covered | `lex/lex_app/celery_tasks.py` (`lex_shared_task` wrapper — cooperative cancel-marker check at task start). Replaces the withdrawn Batch 7r inline guard as the Report 1 (abort→resume) fix: nested calcs keep DISPATCHING by default (7q — the developer's explicit parallelism requirement), and abort-safety comes from the restart-surviving Redis marker `cancel()` already persists (`cluster_cancel_index.mark_cancelled`). `calc_and_save` already checked the marker; decorated calculate methods dispatched via `func.delay` (e.g. a project's `CalculateNAV.calculate`) did not — the uncovered hole. The wrapper now raises `CalculationCancelled` before running anything when a dispatched execution (`context` kwarg with a calculation_id) finds its marker set, so a broker-redelivered child of an aborted calculation lands CANCELLED instead of silently resuming. Sync calls carry no context and never touch the cancel index |
| Test file | `lex/test_project/tests/celery_async/test_8ad_dispatched_task_cancel_marker.py` |
| Test classes | `TestCluster08ad_DispatchedTaskCancelMarker` (8.142 dispatched run + marker set ⇒ raises `CalculationCancelled` before the wrapped function runs, marker consulted for that calculation_id; 8.143 dispatched run + no marker ⇒ marker consulted, function runs exactly once; 8.144 synchronous run (no context kwarg) ⇒ cancel index never consulted, function runs — zero Redis dependency in sync mode) |
| Fixtures | none (module-level `@lex_shared_task` probe function; `_celery_is_active` patched False so calling the descriptor executes the task body in-process, the same code path a worker runs for a redelivered message) |
| Tests landed | 3 pass / 0 fail (8.142–8.144). Regression: full `calculations` + `celery_async` trees = 325 pass / 7 skip / 0 fail |
| Coverage gain | the dispatched-context cancel-marker branch of the `lex_shared_task` wrapper — pinned as the live Report 1 abort→resume regression gate that does NOT sacrifice nested-dispatch parallelism |
| Status | ✅ Complete — source fix + paired tests + plan sync in one change. Allocated `8ad` (next free letter after `8ac`); 8.142 picks up after cluster-8 scenario max 8.141. Companion: Batch 7r withdrawn (inline guard + 7.199 flip + test file removed), 7q restored to committed assertions |

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

### Batch 9f — Core health/calculation/log WebSocket consumers (coverage task #620) ✅

| Property | Value |
| --- | --- |
| Scenario range | 9.37 – 9.42 |
| Type | U |
| Files covered | `lex/api/consumers/BackendHealthConsumer.py`, `lex/api/consumers/CalculationsConsumer.py`, `lex/api/consumers/CalculationLogConsumer.py` |
| Test file | `lex/test_project/tests/signals_ws/test_9f_core_consumers.py` |
| Test classes | `TestCluster09f_BackendHealthConsumer` (9.37 connect/receive health payload, 9.38 disconnect untracks), `TestCluster09f_CalculationsConsumer` (9.39 joins calculations group and forwards ID/notification events, 9.40 disconnect leaves group), `TestCluster09f_CalculationLogConsumer` (9.41 per-record log group and log envelope), `TestCluster09f_ShutdownDisconnectAll` (9.42 shutdown calls `disconnect(None)` on active consumers for all three classes) |
| Fixtures | none — consumer instances with mocked channel layer / socket boundary |
| Tests landed | **6 pass / 0 fail** (direct pytest) |
| Coverage gain | Core consumer connect/disconnect/send branches + `disconnect_all` classmethods for the three coverage-task files |
| Prereqs | none |
| Status | ✅ Complete (Session 81 — June 18). `CalculationLogConsumer.py` is no longer parked: PR #615 wires it in `authenticated_websocket_urlpatterns()`. |

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







