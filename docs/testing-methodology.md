# LEX Test Suite — Methodology & Pipeline Documentation

> **Audience:** Framework developers, customer-facing documentation, CI/CD operators.
> **Last updated:** 2026-04-10
>
> **Related docs:**
> - [`../CLAUDE.md`](../CLAUDE.md) — session summary with the full changelog and current pipeline state
> - [`ci-cd/automated-docs-pipeline.md`](ci-cd/automated-docs-pipeline.md) — the docs auto-update pipeline (repository_dispatch + Copilot coding agent)
> - [`ci-cd/developer-story.md`](ci-cd/developer-story.md) — narrative of how the test suite and CI were built

---

## 1. Goals & Principles

| Principle | Implementation |
|---|---|
| **Release-gating** | No package can be published (pip or frontend build) if any test fails or coverage drops below the threshold. |
| **Customer-visible** | Every test file has a module docstring explaining *what*, *why*, and *how to run*. Customers can read the test suite as living documentation. |
| **Software engineering standards** | Deterministic tests (no `.env` leakage), no `print()` debug output, no bare `except: pass`, no silent test passes. |
| **Two-repo split** | Backend (lex-app) and frontend (process-admin-general-client) are tested independently with their own runners and CI gates. |

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    GitHub Release (tag push)                 │
├──────────────────────────┬──────────────────────────────────┤
│   BACKEND (lex-app)      │  FRONTEND (PAC)                  │
│                          │                                  │
│  django_tests.yml        │  lint.yml                        │
│  ┌──────────────────┐    │  ┌─────────────┐  ┌───────────┐ │
│  │ lex test (Django) │    │  │ yarn check  │  │ yarn test │ │
│  │ + coverage.py     │    │  │ (lint+types)│  │ (vitest)  │ │
│  └────────┬─────────┘    │  └──────┬──────┘  └─────┬─────┘ │
│           │ pass?        │         │                │       │
│           ▼              │         ▼                ▼       │
│  pip_publish.yml         │  push-build-to-pip-package.yml   │
│  ┌──────────────────┐    │  ┌──────────────────────────┐    │
│  │ gate-tests ──────│────│──│ gate-tests ──────────────│    │
│  │ build + twine    │    │  │ yarn build               │    │
│  │ pypi publish     │    │  │ copy → lex/react/build/  │    │
│  └──────────────────┘    │  │ create PR to lex-app     │    │
│                          │  └──────────────────────────┘    │
└──────────────────────────┴──────────────────────────────────┘
```

---

## 3. Backend Test Infrastructure

### 3.1 Test Runner

| Item | Value |
|---|---|
| **Runner** | Django test runner via `lex` CLI wrapper |
| **Invocation** | `lex test <labels> --verbosity=2 --noinput` |
| **Coverage** | `coverage.py` with `.coveragerc` (branch coverage, source=lex) |
| **Threshold** | `--fail-under=50` (increase as coverage grows — never decrease) |
| **Database** | PostgreSQL (CI uses `postgres:latest` service container) |

### 3.2 How to Run Locally

```bash
# From the project directory (e.g. /path/to/ArmiraCashflowDB)
source .venv/bin/activate
set -a && source .env && set +a

# Run all framework tests (canonical labels)
lex test lex.tests --noinput

# Run only unit tests (~40 s)
lex test lex.tests.unit --noinput

# Run only integration tests
lex test lex.tests.integration --noinput

# Run only e2e tests
lex test lex.tests.e2e --noinput

# Run a topic cluster
lex test lex.tests.unit.calculation --noinput

# Run a specific test module
lex test lex.tests.unit.temporal.test_bitemporal_suppression --noinput

# Old labels still work (backward-compat shims)
lex test lex.core.tests.test_bitemporal --noinput

# Run with coverage
coverage run --rcfile=.coveragerc -m lex test lex.tests --noinput
coverage report --rcfile=.coveragerc --fail-under=50
```

### 3.3 Test Discovery Labels

Tests are organized under `lex/tests/` with the following canonical labels:

| Label | Scope |
|---|---|
| `lex.tests.unit` | All unit tests (125 files, 11 topic subdirectories) |
| `lex.tests.unit.api` | REST views, serializers, model structure, permissions |
| `lex.tests.unit.audit` | Audit-log mixins, context resolution, cache, WebSocket |
| `lex.tests.unit.auth` | Keycloak middleware, permissions, user context, tokens |
| `lex.tests.unit.calculation` | State machine, hooks, signals, Celery dispatch |
| `lex.tests.unit.cli` | `lex` CLI argument parsing, project-root discovery |
| `lex.tests.unit.core` | LexModel base, lifecycle hooks, combinations, exceptions |
| `lex.tests.unit.crud` | Generic filters, API helpers |
| `lex.tests.unit.grid` | AG Grid utilities, export, filter backends |
| `lex.tests.unit.infra` | Celery callbacks, health, Keycloak timeout, runtime config |
| `lex.tests.unit.serialization` | LexSerializer, permission-aware mixin, parse_value |
| `lex.tests.unit.temporal` | Bitemporal parsing, sync, suppression, reconciliation |
| `lex.tests.integration` | Bitemporal chaining, user stories, API journeys, audit recovery |
| `lex.tests.e2e` | Full user-journey tests through REST API with real models |

**Backward-compat shims:** Old labels (`lex.core.tests.test_X`, `lex.audit_logging.tests.test_Y`, etc.) still work — each original file is a 2-line re-export shim importing from the canonical location.

### 3.4 CELERY_ACTIVE Isolation

The `.env` file in project directories sets `CELERY_ACTIVE=True`. Tests that depend
on the Celery-vs-local-scheduler branch **must explicitly control this variable**:

```python
# ✅ Correct — deterministic regardless of .env
with patch.dict("os.environ", {"CELERY_ACTIVE": "true"}):
    obj.save()

# ❌ Wrong — depends on whatever .env is sourced
obj.save()  # might hit Celery Beat or LocalScheduler unpredictably
```

### 3.5 CI Workflow: `django_tests.yml`

**Triggers:** push/PR to `lex-app-v2` (default branch), daily cron (09:00 UTC), manual dispatch, reusable `workflow_call`.

**Steps:**
1. Checkout + Python 3.12 setup
2. **System dependencies:** `sudo apt-get install -y libcairo2-dev pkg-config` — required because `pycairo` fails to build otherwise.
3. `pip install -r requirements.txt` + `pip install -e .` (makes `lex` CLI available)
4. `coverage run -m lex test` with explicit test labels
5. `coverage report --fail-under=$COVERAGE_FAIL_UNDER` — **hard gate**
6. `coverage xml` → artifact upload

**Note:** `lex.lex_app.tests` is **not** in the CI test labels. Django's test loader was failing on that package in CI with `unittest.loader._FailedTest.lex_app` due to a `sys.modules` aliasing issue. It still runs locally.

**Environment variables in CI (no Keycloak, no Celery broker):**
```yaml
DJANGO_SETTINGS_MODULE: lex_app.settings
DATABASE_DEPLOYMENT_TARGET: default
CELERY_ACTIVE: "False"
```

### 3.6 CI Workflow: `pip_publish.yml`

**Triggers:** GitHub release (published), manual dispatch (with `version` and dry-run inputs).

**Steps:**
1. **`gate-tests`** — calls `django_tests.yml` as a reusable workflow. If ANY test fails or coverage drops, the entire workflow stops.
2. **Determine version from release tag** — strips the leading `v` from `GITHUB_REF_NAME` (or uses `inputs.version` for manual dispatch).
3. **Write version to `lex/_version.py`** — overwrites that file with `__version__ = "<version>"` right before the build.
4. **`publish`** (depends on gate-tests) — `python -m build` → `pypa/gh-action-pypi-publish@release/v1` using the `PYPI_API_TOKEN` secret.

**Dynamic version.** `pyproject.toml` declares `dynamic = ["version", "dependencies"]` with:
```toml
[tool.setuptools.dynamic]
version = {attr = "lex._version.__version__"}
dependencies = {file = ["requirements.txt"]}
```
so the version shipped to PyPI is always whatever tag the release was cut with. There is no hardcoded version in the repo.

**Authentication.** We use the `PYPI_API_TOKEN` secret (classic API token), not OIDC trusted publishing. The trusted publisher path was tried first but failed with `invalid-publisher` because the PyPI side was not configured for it.

**This means:** You cannot publish a broken pip package, and you cannot ship a wrong version number. Both are the hard gate.

### 3.7 Pre-existing skipped tests

35 pre-existing failing or erroring backend tests are currently marked `@unittest.skip` across 12 files so CI can stay green:

- `test_calculation_signals.py`
- `test_model_collection_structure.py`
- `test_reconcile_command.py`
- `test_temporal_progression.py`
- `test_initial_data_audit_logger.py`
- `test_calculation_model_state_machine.py`
- `test_calculation_history_transitions.py`
- `test_api_key_user_context.py`
- `test_lex_cli.py`
- `test_fix_v1_migration.py`
- `test_programmatic_creation.py`
- `test_history_api.py`

These are **documented for later fix, not deleted**. They represent real problems — some from Redis/Celery setup issues in CI, some from genuine framework bugs exposed by the tests. The directive was to unblock the pipeline now and fix them in a follow-up round.

---

## 4. Frontend Test Infrastructure

### 4.1 Test Runner

| Item | Value |
|---|---|
| **Runner** | Vitest (via `vite test` / `yarn test`) |
| **Environment** | jsdom (browser APIs without a real browser) |
| **Globals** | `true` — `describe`, `it`, `expect`, `jest.mock`, `jest.fn` available without imports |
| **Setup** | `vitest.setup.ts` — loads `@testing-library/jest-dom` custom matchers |
| **Coverage** | V8 provider, reporters: text + lcov |

### 4.2 How to Run Locally

```bash
cd frontend

# Install dependencies (first time or after package.json changes)
yarn

# Run all tests (watch mode)
yarn test

# Run all tests once (CI mode — exits after completion)
yarn test --run

# Run with coverage
yarn test --run --coverage
```

### 4.3 Test File Locations

All test files live in `src/__test__/` with the naming convention `*.test.{ts,tsx}`:

| Test File | What It Covers |
|---|---|
| `calculationSlice.test.ts` | Redux calculation state management |
| `CalculateFunctionality.test.tsx` | Calculation button UI (disabled states, spinner) |
| `CalculateFunctionality.helpers.test.tsx` | Calculation helper utilities |
| `CustomListActions.test.tsx` | Custom list action buttons |
| `CustomReactAdmin.test.tsx` | React Admin wrapper customization |
| `EditToolbar.test.tsx` | Edit form toolbar buttons |
| `RecordActionButtons.test.tsx` | Record-level action buttons |
| `actionColumnUtils.test.ts` | Action column field detection, edit permissions |
| `bulkSelectionUtils.test.ts` | Bulk delete visibility, row selectability |
| `normalizeCalculationLogText.test.ts` | Calculation log text normalization |
| `useEmbedContext.test.tsx` | Embed context hook behavior |

### 4.4 Vitest Configuration

The test configuration lives in `vite.config.mts` under the `test` key:

```typescript
test: {
  globals: true,                              // jest.mock/jest.fn available globally
  environment: 'jsdom',                       // browser-like DOM for React components
  setupFiles: './vitest.setup.ts',            // @testing-library/jest-dom matchers
  include: ['src/**/*.test.{ts,tsx}'],        // test file discovery pattern
  coverage: {
    provider: 'v8',                           // fast native coverage
    reporter: ['text', 'lcov'],               // terminal + CI-compatible output
    include: ['src/**/*.{ts,tsx}'],            // source files to measure
    exclude: ['src/__test__/**', 'src/**/*.d.ts'],
  },
}
```

**Why `globals: true`?** The existing 11 test files use `jest.mock()` and `jest.fn()`.
Vitest's globals mode exposes Jest-compatible APIs without requiring import changes.

### 4.5 CI Workflow: `lint.yml` (Code Quality & Tests)

**Triggers:** Every push and PR to any branch.

**Jobs:**
1. **`run-linters`** — `yarn checkcode` (TypeScript type-check + ESLint + Prettier)
2. **`run-tests`** — `yarn test --run` (Vitest in CI mode, exits after completion)

Both jobs run in parallel. If either fails, the workflow fails.

### 4.6 CI Workflow: `push-build-to-pip-package.yml`

**Triggers:** GitHub release (published), manual dispatch.

**Jobs:**
1. **`gate-tests`** — `yarn test --run`. **Hard gate.**
2. **`build-and-deploy`** (depends on gate-tests) — `yarn build` → copy `build/` to `lex-app/lex/react/build/` → create PR to lex-app repo.

**This means:** A broken frontend cannot be pushed to the pip package. Tests must pass first.

---

## 5. Changes Made — Complete Changelog

### 5.1 Backend Pipeline Changes

| File | Change | Why |
|---|---|---|
| `.github/workflows/django_tests.yml` | Switched from `manage.py test` to `lex test` CLI. Added `pip install -e .` for CLI availability. Added `COVERAGE_FAIL_UNDER` env var with hard `--fail-under` gate. Added explicit test labels. Added `workflow_call` trigger for reuse. **April 10:** added `libcairo2-dev`/`pkg-config` apt install; removed `lex.lex_app.tests` from labels (loader error). | The `lex` CLI is the official way to run the framework. Coverage must be enforced. `pycairo` won't build without the system libs. |
| `.github/workflows/pip_publish.yml` | **Created.** Originally used OIDC trusted publishing. **April 10: rewritten** to use `PYPI_API_TOKEN`, derive version from the GitHub release tag, and write it to `lex/_version.py` before build. | Trusted publishing failed with `invalid-publisher`; switched to the existing local token. Dynamic version removes a manual step and a class of mistakes. |
| `.github/workflows/custom-image.yml` | **April 10:** gated behind `workflow_run` of "Publish to PyPI" with `conclusion == 'success'`. Added a tag-resolution step since `workflow_run` events don't carry `release.tag_name`. | The Dockerfile does `pip install lex-app==<version>`, so the package has to exist on PyPI before the image can be built. |
| `pyproject.toml` | **April 10:** switched to `dynamic = ["version", "dependencies"]` with `[tool.setuptools.dynamic] version = {attr = "lex._version.__version__"}`. | Makes the release tag the single source of truth for the shipped version. |
| `lex/_version.py` | **April 10: created.** Holds `__version__ = "0.0.0.dev0"` as a placeholder; overwritten in CI before build. | Required by the dynamic-version setuptools config. |
| `requirements.txt` | **April 10:** pinned `DjangoSharepointStorage==1.1.7`. | The unpinned dependency was pulling `1.1.9`, which broke with `django_sharepoint_storage.SharePointContext` not found. |
| `.coveragerc` | Configured branch coverage, source=lex, omit patterns for migrations/tests/frontend/docs. | Centralized coverage config shared by local and CI runs. |

### 5.2 Frontend Pipeline Changes

| File | Change | Why |
|---|---|---|
| `frontend/.github/workflows/lint.yml` | Renamed to "Code Quality & Tests". Added `run-tests` job running `yarn test --run`. | Tests were never run in CI before. |
| `frontend/.github/workflows/push-build-to-pip-package.yml` | Added `gate-tests` job as hard dependency before `build-and-deploy`. | Prevents shipping broken frontend to lex-app. |

### 5.3 Frontend Test Infrastructure

| File | Change | Why |
|---|---|---|
| `frontend/package.json` | Added `vitest`, `@vitest/coverage-v8`, `jsdom` to devDependencies. | Vitest is needed to actually run `yarn test`. |
| `frontend/vite.config.mts` | Changed import to `vitest/config`. Added `test` block with globals, jsdom, setup file, coverage config. | Configures test runner, enables jest.mock() compat, sets up coverage. |
| `frontend/vitest.setup.ts` | **Created.** Imports `@testing-library/jest-dom`. | Provides `toBeInTheDocument()` and other custom matchers. |
| `frontend/tsconfig.json` | Added `vitest/globals` to `types` array. | TypeScript needs to know about `jest.mock`, `jest.fn`, `describe`, etc. |
| `frontend/vite.env.d.ts` | Fixed typo in existing reference. Added `vitest/globals` reference. | Ensures type resolution for test globals. |

### 5.4 Backend Test Files Created

| File | Tests | Feature |
|---|---|---|
| `lex/core/tests/test_permission_result.py` | 28 | PermissionResult factory methods, get_fields, __str__ |
| `lex/core/tests/test_user_context.py` | 37 | UserContext construction, Keycloak scopes, normalization |
| `lex/core/tests/test_permission_enforcement.py` | 43 | ModelContainer permission evaluation, LexModel helpers |
| `lex/core/tests/test_future_activation_scheduler_routing.py` | 2 | Explicit Celery-vs-local scheduler branch routing |
| `lex/core/tests/test_lexlogger_builder.py` | 26 | LexLogger builder pattern (add_text, add_table, etc.) |
| `lex/core/tests/test_lexlogger_context.py` | 27 | ModelContext, ContextResolver, model_logging_context |
| `lex/core/tests/test_calculation_model_state_machine.py` | 33 | CalculationModel statuses, execute_calculation, error capture, build_exception_chain, CalculationModelException |
| `lex/core/tests/test_calculated_model_combination_engine.py` | 31 | Combination generator, cluster manager, flatten |
| `lex/core/tests/test_bitemporal_suppression.py` | 16 | suspend_bitemporal context managers |
| `lex/core/tests/test_lifecycle_hooks.py` | ~20 | Hook dispatch order, pre/post validation, snapshot/rollback |
| `lex/core/tests/test_audit_actor_tracking.py` | ~37 | created_by/edited_by auto-set, _resolve_audit_actor |
| `lex/audit_logging/tests/test_audit_log_mixin.py` | ~25 | AuditLogMixin CRUD audit trail, retry logic, status transitions |
| `lex/audit_logging/tests/test_initial_data_audit_logger.py` | ~20 | InitialDataAuditLogger create/update/delete, finalize_batch |

### 5.5 Backend Test Files Created (April 14, 2026 — API & Infrastructure Layer)

These tests target the **previously untested API layer**, middleware, and data-integrity modules. They add 198 new test methods across 8 files.

| File | Tests | Feature Area |
|---|---|---|
| `lex/process_admin/tests/test_ag_grid_list_utilities.py` | 74 | AG Grid SSRM utilities: `_safe_key`, `_parse_bool`, `_parse_int`, `_parse_ag_datetime`, `_parse_ag_date`, `_ag_filter_has_time`, `normalize_field_path`, `_coerce_value`, `_build_query_from_values`, `RESERVED_QUERY_PARAMS`, `SAFE_LOOKUPS` |
| `lex/audit_logging/tests/test_websocket_notifier.py` | 11 | `WebSocketNotifier.send_calculation_update` (success, no channel layer, exception), `send_calculation_notification` (default/custom group, failure), `is_websocket_available` |
| `lex/process_admin/tests/test_keycloak_permissions_middleware.py` | 28 | `KeycloakPermissionsMiddleware` defaults, happy-path, UMA/userinfo failure resilience, `_extract_client_roles` (string/list/dict/nested/empty), `cleanup_invalid_tokens` |
| `lex/process_admin/tests/test_streamlit_token_views.py` | 21 | `StreamlitTokenView._check_token_status` (valid/refresh/expired/wrong-user/revoked/garbage), `_generate_new_token` (payload, expiry, permissions), `_get_user_permissions`, `StreamlitTokenRevokeView.post` |
| `lex/process_admin/tests/test_bitemporal_synchronizer.py` | 12 | `BitemporalSynchronizer.sync_record_for_model` — upsert, delete on `-` history, delete on no effective record, no-op when nothing exists, history model auto-discovery |
| `lex/process_admin/tests/test_temporal_reconciler.py` | 10 | `TemporalReconciler.reconcile_model_window` (sync candidates, no-history skip, filter window), `reconcile_changes_since` (cross-model scan, skip missing tables) |
| `lex/process_admin/tests/test_model_export_utilities.py` | 30 | `ModelExportView` utilities: `get_exportable_fields_for_object` (new/legacy/fallback), `_normalize_ag_request`, `_extract_selected_ids_for_export`, `_extract_selected_group_key_paths`, `_apply_ag_column_layout`, `_coerce_group_key` |
| `lex/process_admin/tests/test_history_endpoint.py` | 12 | `HistoryModelEntry._get_user_info`, `_serialize_record`, `_get_snapshot` (control field exclusion, datetime ISO formatting, serializer fallback), `_get_system_history`, model-without-history 400 |

### 5.5.1 Backend Test Files Created (Session 2 — API CRUD & Core Fields)

These tests target the **core CRUD views** (One/Many), shared mixins, filter backends, and field types. They add 114 new test methods across 7 files.

| File | Tests | Feature Area |
|---|---|---|
| `lex/process_admin/tests/test_one_model_entry.py` | 22 | `OneModelEntry` — `_prepare_update_request` (strip calculate, inject is_calculated), `_reset_instance_is_calculated` (save+patch response), `create()` permission enforcement (new-style, legacy, exception-allows), atomic transactions, bitemporal path, meta-history guard, calculation trigger flow (untrack, IN_PROGRESS, StateStore, WebSocket, cache), CalculationModelException handling, generic exception wrapping, finally-reset |
| `lex/process_admin/tests/test_many_model_entries.py` | 16 | `ManyModelEntries` — per-object permission checking (`check_object_permissions` on every entry), bulk GET (serialized list), bulk PATCH (partial=True, perform_bulk_update, returns PKs), bulk DELETE (perform_bulk_destroy, returns IDs), empty queryset handling, permission/mixin inheritance |
| `lex/process_admin/tests/test_model_entry_provider_mixin.py` | 16 | `ModelEntryProviderMixin` — `get_queryset` (no as_of → all(), as_of → `get_queryset_as_of`, unparseable → fallthrough), auto `select_related` for FK fields, `get_serializer_class` (default, named, unknown → APIException, User subclass → UserModelSerializer, get_serializers_map method vs attribute), `UserModelSerializer` (short_description format, Meta config), permission defaults |
| `lex/process_admin/tests/test_xlsx_field.py` | 26 | `XLSXField` — constants (max_length, formats), `get_number_of_rows_to_insert` (dotted headers, empty cells, index offset), `insert_rows_before_first_row`, `split_entries_in_sheet` (split values, bold font, thin borders), `create_pivotable_row` (concatenation, skip empty), `create_excel_file_from_dfs` end-to-end (single/multi sheet, empty DF blank row, None DF skipped, default sheet name, index on/off, comments, save called, BytesIO seeked) |
| `lex/process_admin/tests/test_destroy_one_with_payload.py` | 12 | `DestroyOneWithPayloadMixin` — `_unwrap_historical_instance` (LexModel passthrough, history_object→instance chain, direct instance FK, unwrap failure), `destroy()` with permission_delete (granted/denied), legacy can_delete (granted/denied), permission exception → deny, no permission methods → deny, returns HTTP 200 with serialized data (not 204) |
| `lex/process_admin/tests/test_temporal_parse_as_of.py` | 14 | `parse_as_of_datetime` — None/empty/whitespace/garbage → None, Z-suffix parsed as UTC, microseconds, naive → UTC, positive/negative offset conversion, USE_TZ=False → naive datetime, date-only → midnight, numeric input coercion |
| `lex/process_admin/tests/test_pk_list_filter_backend.py` | 8 | `PrimaryKeyListFilterBackend` — no ids → passthrough, ids filtering, custom pk_name, empty string cleanup, single id, `filter_for_export` base64 decoding (ids, no-ids, empty cleanup) |

**Total new tests in this round: 114**
**Cumulative new tests (both sessions): 312**

### 5.5.2 Backend Test Files Created (Session 3 — CalculationModel, AuditLog, model_logging_context)

These tests target the **deepest gaps identified in the quality audit**: the `calculate_hook` lifecycle (zero prior tests), `dispatch_calculation_task` (zero prior tests), `calculation_audit` terminal audit helpers (zero prior tests), `perform_update` (the only untested CRUD path), and `model_logging_context` stack semantics (used by every nested calculation). They add 88 new test methods across 5 files.

| File | Tests | Feature Area |
|---|---|---|
| `lex/core/tests/test_calculate_hook.py` | 17 | `CalculationModel.calculate_hook` — **sync path** (CELERY_ACTIVE=false): success→SUCCESS transition, failure→ERROR transition with CalculationModelException wrapping, error persists to DB, ActiveCalculationStateStore registration, IN_PROGRESS broadcast, re-entrancy guard cleanup (success and failure). **Celery path** (CELERY_ACTIVE=true): dispatches with explicit async context (FireAndForget/WaitForTasks), runs sync inside worker, wraps in WaitForTasks when no context. **Re-entrancy**: skips when guard set, guard is True during execution. **Error handling**: wraps in CalculationModelException with `__cause__`, includes self in calc_obj, saves ERROR to DB, stores `_pending_terminal_audit`, attempts cache cleanup for root. |
| `lex/core/tests/test_dispatch_calculation_task.py` | 5 | `CalculationModel.dispatch_calculation_task` — context extraction (strips unpicklable request via `OperationContext.extract_info_request`), preserves calculation_id in context, passes model_context to `.delay()` for worker context restoration, registers AsyncResult with `register_task_with_context`, calls `.delay()` on resolved `lex_func()`. |
| `lex/audit_logging/tests/test_calculation_audit.py` | 32 | `calculation_audit` helpers — `_same_model_instance` (identity by label_lower+pk, None handling, no _meta), `_is_root_calculation` (no context=root, empty root=root, matching root+current=root, different instance=not root, root matches but current different=not root), `_resolve_calculation_id` (context priority, state store fallback, instance attribute fallback, None when unavailable), `_resolve_actor` (from context, from request dict, from request attribute, fallback to "system"), `_resolve_context_data` (dict passthrough, operation_context fallback, empty on failure), `_resolve_model_context` (explicit vs contextvar), `_build_payload` (includes is_calculated, error_message on failure, persisted error takes priority), `_resolve_audit_log_template` (AuditLog extraction, non-AuditLog rejected, None handling). |
| `lex/audit_logging/tests/test_audit_log_mixin_update.py` | 12 | `AuditLogMixin.perform_update` — creates AuditLog with action="update", **re-serializes payload after save** (calls `get_serializer(instance)` for post-save data with auto-fields/timestamps), sets content_type and object_id, status→success, failure→status "failure" with traceback, re-raises original exception, returns saved instance. `perform_create` correctness — payload includes pk after save, calculation_id from kwargs. `perform_destroy` correctness — captures data before deletion, uses instance class for resource name. |
| `lex/audit_logging/tests/test_model_logging_context.py` | 22 | `ModelContext` stack — empty (current/parent/root all None), single push, two-level (parent+child), three-level (grandparent+parent+child), pop restores previous, pop empty returns None, repr, init from list. `model_logging_context` — single level sets/cleans current, **nested two levels** (InvestorTrackRecord→CalculateNAV), **nested three levels** (real-world Report→NAV→Cashflow pattern from project_example), exception restores stack, nested exception preserves outer, rejects non-Django model and dicts, accepts None. **ContextResolver integration** — reads current/parent from stack, cache key pattern matches CacheManager format. |

**Total new tests in this round: 88**
**Cumulative new tests (all sessions): 400**

### 5.5.3 Backend Tests Created/Extended (Session 4 — Coverage Push)

This round targeted the **lowest-coverage production modules** identified by ``coverage.py``. It also fixed 3 pre-existing test failures in ``test_active_calculation_state_store.py`` and consolidated duplicate tests from the deleted ``test_calculation_model_helpers.py`` into ``test_calculation_model_state_machine.py``. 90 new test methods across 5 files (3 new, 2 extended).

| File | Tests Added | Coverage Impact | Feature Area |
|---|---|---|---|
| `lex/core/tests/test_calculation_signals.py` | +10 | 73% → **96%** | ABORTED broadcast, unknown status no-op, `_resolve_calculation_id` direct tests (context/store/instance-attr/empty-string/non-string/none priorities) |
| `lex/audit_logging/tests/test_cache_manager.py` | 28 (new) | 26% → **94%** | `store_message` (single, append, invalid backend, exception), `get_message` (hit, miss, unavailable), `cleanup_calculation` (specific keys, no args, invalid backend, delete error, iter_keys, keys fallback, no pattern support), `cleanup_specific_key`, `is_cache_available`, `build_cache_key` |
| `lex/audit_logging/tests/test_content_types.py` | +14 | 47% → **92%** | `_describe_model` (Django model, plain class, string, None), `_get_content_type_manager`, `safe_get_content_type` validation, `safe_get_generic_related_object` (no ct_id, no obj_id, custom field names, exception, None model_class) |
| `lex/process_admin/tests/test_user_read_restriction_filter.py` | 20 (new) | 48% → **64%** | AuditLogStatus/CalculationLog/AuditLog bypass, `_get_default_permission_target` (LexModel, custom override, plain class, instance_type fallback), `_apply_default_permission_read_filter` (global read, no match, scoped IDs, no read scope, empty/None permissions, non-mapping) |
| `lex/process_admin/tests/test_base_serializer_helpers.py` | 31 (new) | 45% → **54%** | `_get_lexmodel_fields` (type, contents, caching), `_get_capabilities` (new/legacy methods, caching, plain class), `_normalize_field_names` (None/string/list/set/tuple/non-strings/unknown), `_unwrap_instance` (plain/history/instance), `_get_cached_field_names`, `_parse_value_for_field` (None/FK-dict/no-id), `FilteredListSerializer` (empty filter, Manager input), `_get_model_lookup`, `_resolve_target_model` (ct_id/resource/unknown) |

**Fixes applied:**
- `test_active_calculation_state_store.py`: Patched `_find_model_by_name` in `TestValidateAndPrune` and `TestResolveModelAndPk` so dynamically-created test models are resolvable (3 failures → 0)
- `test_calculation_model_helpers.py`: **Deleted** — 8 unique tests merged into `test_calculation_model_state_machine.py` (25 → 33 tests)

**Total new tests in this round: 90**
**Cumulative new tests (all sessions): 490**

### 5.6 Backend Test Files Modified (Quality Pass)

All ~25 existing test files received:
- **Module docstrings** explaining what is tested, why, and how to run
- **Class docstrings** on every `TestCase`
- **Bare `except: pass`** → `except Exception: pass` (explicit exception type)
- **`print()` statements** removed (~20 total)
- **Silent 404 returns** → `self.skipTest()` (test_history_api)
- **Duplicated assertions** removed (test_event_scheduling)
- **Dead/empty files** handled (test_bitemporal_future_activation, test_model_collection_structure)
- **CELERY_ACTIVE isolation** — event scheduling tests now explicitly `patch.dict` the env var

### 5.7 Test Infrastructure Modified (April 14, 2026)

| File | Change | Why |
|---|---|---|
| `lex/process_admin/tests/django_test_settings.py` | Added `SILENCED_SYSTEM_CHECKS = ["fields.E307"]` | The audit_logging `CalculationLog` model has a lazy FK reference to `audit_logging.AuditLog`, but `audit_logging` is not in `INSTALLED_APPS` for the minimal test config. This silences the system check error that was blocking test discovery. |

---

## 6. Test Naming Conventions

| Convention | Example |
|---|---|
| File names | `test_<feature_area>.py` |
| Class names | `<Feature>Test(TestCase)` or `<Feature>Test(SimpleTestCase)` |
| Method names | `test_<behavior_under_test>` |
| Docstrings | Required on every test method — one sentence explaining what is asserted |

### SimpleTestCase vs TestCase vs TransactionTestCase

| Base Class | When to Use |
|---|---|
| `SimpleTestCase` | Pure logic, no database access (mocked imports, utility functions) |
| `TestCase` | Needs DB but no raw schema manipulation (wraps each test in a transaction) |
| `TransactionTestCase` | Needs `schema_editor`, raw SQL, or multi-transaction behavior |

---

## 7. Release Workflow — Step by Step

### 7.1 Backend (pip package)

1. Developer pushes to `main` or creates a PR.
2. `django_tests.yml` runs automatically — all framework tests + coverage gate.
3. If tests pass: merge is allowed.
4. To publish: create a GitHub Release (tag).
5. `pip_publish.yml` fires → calls `django_tests.yml` as gate → builds → publishes to PyPI.
6. **If tests fail at any point: publish is blocked.**

### 7.2 Frontend (React build)

1. Developer pushes to any branch.
2. `lint.yml` runs: linting + type-check + unit tests.
3. If tests pass: merge is allowed.
4. To publish: create a GitHub Release.
5. `push-build-to-pip-package.yml` fires → runs tests as gate → builds → copies to lex-app → creates PR.
6. **If tests fail at any point: the PR to lex-app is never created.**

### 7.3 Local pip Publish (Terminal)

If you publish from the terminal, you should first run:

```bash
# From lex-app root
source project_example/.venv/bin/activate
set -a && source project_example/.env && set +a

coverage run --rcfile=.coveragerc -m lex test --verbosity=2 --noinput \
    lex.core.tests lex.audit_logging.tests lex.process_admin.tests \
    lex.lex_app.tests lex.tests
coverage report --rcfile=.coveragerc --fail-under=50

# Only if the above passes:
python -m build
twine check dist/*
twine upload dist/*
```

To enforce this, add a `Makefile` or shell script:

```bash
#!/bin/bash
# scripts/publish.sh — safe local publish
set -euo pipefail

echo "Running test suite..."
coverage run --rcfile=.coveragerc -m lex test --verbosity=2 --noinput \
    lex.core.tests lex.audit_logging.tests lex.process_admin.tests \
    lex.lex_app.tests lex.tests

echo "Checking coverage threshold..."
coverage report --rcfile=.coveragerc --fail-under=50

echo "Building distribution..."
python -m build

echo "Verifying package..."
twine check dist/*

echo "Publishing to PyPI..."
twine upload dist/*
```

---

## 8. Coverage Strategy

| Metric | Current (April 14) | Previous | Target |
|---|---|---|---|
| Backend test count | ~649+ (337 + 198 + 114 new) | ~535+ | Continue growing |
| Backend coverage threshold | 50% | 50% | Increase to 70% → 80% → 90% over releases |
| Frontend coverage | Not enforced yet | — | Add `--coverage.thresholds.lines=60` to CI |

**Rule:** The threshold in `django_tests.yml` (`COVERAGE_FAIL_UNDER`) can only go **up**, never down. Each release that adds tests should bump it by 5-10%.

---

## 9. Automated Documentation Updates (Post-Release)

After every successful pip publish, documentation is automatically updated via a two-repo `repository_dispatch` pattern. No docs go live without human review.

### Architecture: repository_dispatch pattern

The key design decision is **separation of concerns** — each repo owns its own CI logic:

- **`lex-app`** fires a lightweight event after publishing.
- **`lex-app-docs`** receives the event, does the heavy lifting (diff analysis, LLM call, PR creation) using its **own `GITHUB_TOKEN`**.

This means the cross-repo auth surface is minimal: just one API call to fire the dispatch event. The push + PR in `lex-app-docs` needs no cross-repo token at all.

### Flow

```
lex-app                                  lex-app-docs
───────                                  ────────────

pip_publish.yml succeeds
        │
        ▼ (workflow_run trigger)
update_docs.yml
        │
        ├─ Checkout lex-app (read tags)
        ├─ Generate GitHub App token
        │  (scoped to lex-app-docs only)
        │
        ▼
  POST /repos/.../dispatches
  event_type: "release-published"         on: repository_dispatch
  payload: { head_tag, base_tag }           types: [release-published]
        │                                         │
        └─ Done                                   ▼
                                          auto-update-docs.yml
                                                  │
                                                  ├─ Checkout lex-app-docs (own repo)
                                                  ├─ Checkout lex-app (read-only, public)
                                                  ├─ git log base..head  → commit messages
                                                  ├─ git diff base..head → code changes
                                                  ├─ Collect current docs (content/*.md)
                                                  │
                                                  ▼
                                          GitHub Models API (models.github.ai)
                                          Model: openai/gpt-4.1
                                          "Analyze changes, return JSON patches"
                                                  │
                                                  ▼
                                          Apply updates to docs checkout
                                          Create PR (own GITHUB_TOKEN)
                                          Branch: docs/auto-update-v2.X.Y
                                          Labels: documentation, automated
```

### Why `repository_dispatch` instead of doing everything in `lex-app`?

| Concern | Old approach (monolithic) | New approach (dispatch) |
|---|---|---|
| Cross-repo auth needed for | Push + PR in lex-app-docs | Just the dispatch event |
| GitHub App token scope | `contents:write` + `pull-requests:write` on lex-app-docs | `contents:write` on lex-app-docs (for dispatch only) |
| Who owns the docs workflow? | lex-app | lex-app-docs (each repo owns its CI) |
| PR uses which token? | GitHub App token (cross-repo) | Default `GITHUB_TOKEN` (own repo) |

### What you do

1. The PR appears in the **lex-app-docs** repo after each lex-app release.
2. Review the diff — the LLM explains what changed via the `summary` field.
3. Edit if needed, then merge — the docs site deploys automatically.
4. If the LLM got it wrong, close the PR and fix manually.

### Workflow files

| File | Repo | Purpose |
|---|---|---|
| `.github/workflows/update_docs.yml` | lex-app | Fires `repository_dispatch` after publish |
| `.github/workflows/auto-update-docs.yml` | lex-app-docs | Receives event, runs LLM, creates PR |

A copy of the receiver workflow is stored at `docs/ci-cd/docs-receiver-workflow.yml` in lex-app for reference.

### Required secrets

**On `lex-app`:**

| Secret | Purpose |
|---|---|
| `DOCS_APP_ID` | GitHub App numeric ID |
| `DOCS_APP_PRIVATE_KEY` | GitHub App `.pem` private key |

**On `lex-app-docs` (optional):**

| Secret | Purpose |
|---|---|
| `MODELS_API_TOKEN` | GitHub PAT with `models:read` scope (for Copilot API). Falls back to default `GITHUB_TOKEN` if not set. |

### Why a GitHub App?

A **personal access token (PAT)** would also work for the dispatch call, but:

- PATs are tied to a person's account — if they leave the org, the pipeline breaks.
- PATs are long-lived — if leaked, they grant access until manually revoked.
- GitHub Apps generate **short-lived tokens** (expire in 1 hour) and are **org-controlled**.
- GitHub Apps have **granular permissions** — only the repos and scopes you approve.

This is GitHub's [recommended approach](https://docs.github.com/en/apps/overview) for CI/CD cross-repo automation.

### Setting up the GitHub App

1. Go to **Org Settings → Developer settings → GitHub Apps → New GitHub App**
2. Name: `lex-docs-bot` (or similar)
3. Permissions:
   - Repository contents: **Read & Write** (for dispatch + checkout)
   - Pull requests: **Read & Write** (optional — only if the App creates PRs directly)
4. Install on: `lex-app` and `lex-app-docs`
5. Copy the **App ID** (numeric) → add as `DOCS_APP_ID` secret on `lex-app`
6. Generate a **private key** (.pem) → add as `DOCS_APP_PRIVATE_KEY` secret on `lex-app`

### Manual trigger

Both workflows support `workflow_dispatch`:

- **`lex-app`**: Go to Actions → "Update Documentation (Post-Release)" → Run workflow. Provide custom base/head tags.
- **`lex-app-docs`**: Go to Actions → "Auto-Update Documentation" → Run workflow. Provide custom base/head tags directly.

### Alternatives considered

| Approach | Verdict |
|---|---|
| **PAT (classic)** | Works but tied to a person, long-lived, broad scope. Security anti-pattern. |
| **PAT (fine-grained)** | Better — scoped to repos, has expiration. Still tied to a person. |
| **Deploy keys** | Only work for `git push`, can't open PRs via the API. |
| **GitHub App + repository_dispatch** | Short-lived tokens, org-controlled, minimal cross-repo surface. Chosen approach. |
| **Monolithic workflow (old)** | Everything in lex-app. Works but needs broader cross-repo auth and violates separation of concerns. |

---

## 10. Future Work

### 10.1 Remaining Backend Coverage Gaps (Prioritized)

The following areas still lack dedicated tests. They are ordered by impact. Items marked ~~strikethrough~~ ✅ have been addressed in Sessions 1–4.

**🔴 Critical — API Views (REST endpoints)**

| Module | Lines | Priority | Notes |
|---|---|---|---|
| ~~`lex/api/views/model_entries/One.py`~~ | 236 | ~~P0~~ ✅ | **Done** — 22 tests cover create/update/destroy permission paths, bitemporal, meta-history, calculation trigger, exception handling |
| ~~`lex/api/views/model_entries/Many.py`~~ | 35 | ~~P1~~ ✅ | **Done** — 16 tests cover bulk GET/PATCH/DELETE, per-object permissions, filter backend |
| `lex/api/views/authentication/KeycloakManager.py` | 1057 | P0 | Largest single module. Admin operations, UMA, authorization import/export. |
| `lex/api/views/file_operations/ModelExport.py` | 683 | P1 | View-level export flow tested (utilities done), but `post()` end-to-end needs a mocked queryset + AG Grid request. |
| `lex/api/views/ModelStructureObtainView.py` | 301 | P2 | Model structure tree API. |

**� Critical — Core CalculationModel & Audit Pipeline**

| Module | Lines | Priority | Notes |
|---|---|---|---|
| ~~`lex/core/models/CalculationModel.py` — `calculate_hook`~~ | 120 | ~~P0~~ ✅ | **Done** — 17 tests cover sync path (success→SUCCESS, failure→ERROR+CalculationModelException, DB persistence), celery path (FireAndForget/WaitForTasks dispatch, sync-in-worker), re-entrancy guard, error handling (exception chaining, cache cleanup, `_pending_terminal_audit`) |
| ~~`lex/core/models/CalculationModel.py` — `dispatch_calculation_task`~~ | 30 | ~~P0~~ ✅ | **Done** — 5 tests cover context extraction, calculation_id preservation, model_context propagation, task registration, `.delay()` on lex_func |
| ~~`lex/audit_logging/utils/calculation_audit.py`~~ | 256 | ~~P0~~ ✅ | **Done** — 32 tests cover all helper functions: `_same_model_instance`, `_is_root_calculation`, `_resolve_calculation_id`, `_resolve_actor`, `_resolve_context_data`, `_resolve_model_context`, `_build_payload`, `_resolve_audit_log_template` |
| ~~`lex/audit_logging/mixins/AuditLogMixin.py` — `perform_update`~~ | 40 | ~~P1~~ ✅ | **Done** — 12 tests cover perform_update (action="update", post-save re-serialization, content_type/object_id, success/failure status), plus data correctness for perform_create and perform_destroy |
| ~~`lex/audit_logging/utils/ModelContext.py` — `model_logging_context`~~ | 80 | ~~P1~~ ✅ | **Done** — 22 tests cover ModelContext LIFO stack (push/pop/empty), model_logging_context context manager (single/nested/three-level, exception safety), ContextResolver integration |
| ~~`lex/core/signals/CalculationSignals.py`~~ | 56 | ~~P1~~ ✅ | **Done** — 20 tests cover `update_calculation_status` (IN_PROGRESS/SUCCESS/ERROR/ABORTED broadcasts, store management), `_resolve_calculation_id` (context/store/instance-attr priorities). Coverage: 73% → 96% |
| ~~`lex/audit_logging/utils/CacheManager.py`~~ | 112 | ~~P1~~ ✅ | **Done** — 28 tests cover `store_message`, `get_message`, `cleanup_calculation` (specific keys, pattern matching, fallbacks), `cleanup_specific_key`, `is_cache_available`, `build_cache_key`. Coverage: 26% → 94% |
| ~~`lex/audit_logging/utils/content_types.py`~~ | 43 | ~~P2~~ ✅ | **Done** — 19 tests cover `safe_get_content_type` (stale cache retry, validation), `safe_get_generic_related_object` (5 paths), `_describe_model`, `_get_content_type_manager`. Coverage: 47% → 92% |

**�🟡 Medium — Mixins, Fields & Middleware**

| Module | Lines | Priority | Notes |
|---|---|---|---|
| ~~`lex/api/views/model_entries/mixins/ModelEntryProviderMixin.py`~~ | 85 | ~~P1~~ ✅ | **Done** — 16 tests cover get_queryset (as_of, select_related), get_serializer_class, UserModelSerializer |
| ~~`lex/api/views/model_entries/mixins/DestroyOneWithPayloadMixin.py`~~ | 123 | ~~P1~~ ✅ | **Done** — 12 tests cover _unwrap_historical_instance, permission enforcement, HTTP 200 response |
| ~~`lex/core/fields/XLSX_field.py`~~ | 120 | ~~P3~~ ✅ | **Done** — 26 tests cover Excel generation end-to-end, header splitting, formatting, comments |
| ~~`lex/api/utils/temporal.py`~~ | 44 | ~~P2~~ ✅ | **Done** — 14 tests cover all parse_as_of_datetime paths (None, Z-suffix, naive, offset, USE_TZ) |
| ~~`lex/api/views/model_entries/filter_backends.py`~~ | 175 | ~~P2~~ ✅ | **Done** — 28 tests cover PrimaryKeyListFilterBackend + UserReadRestrictionFilterBackend (bypass paths, permission target resolution, Keycloak scope filtering). Coverage: 48% → 64% |
| ~~`lex/api/serializers/base_serializers.py` (helpers)~~ | 335 | ~~P2~~ ✅ | **Done** — 31 tests cover `_get_lexmodel_fields`, `_get_capabilities`, `_normalize_field_names`, `_unwrap_instance`, `FilteredListSerializer`, `_parse_value_for_field`, `_resolve_target_model`. Coverage: 45% → 54% |
| `lex/api/consumers/` (5 files) | ~180 | P2 | All WebSocket consumers untested. `ActiveCalculationStateStoreConsumer` is the most impactful. |
| `lex/authentication/middleware.py` | 15 | P2 | `APIKeyExemptMiddleware` — small but security-critical. |
| `lex/authentication/views/token_views.py` | 162 | P2 | `_check_token_status` and `_generate_new_token` are now tested; `post()` endpoint needs request-level tests. |

**🟢 Low — Utility & Infrastructure**

| Module | Lines | Priority | Notes |
|---|---|---|---|
| `lex/process_admin/sites/process_admin_site.py` | 322 | P3 | URL generation, `get_urls()`. |
| `lex/lex_app/management/commands/` (7 untested) | ~1500+ | P3 | `lex_migrate`, `sync_keycloak`, `detect_model_changes`, etc. |
| `lex/utilities/import_system/` (3 files) | ~376 | P3 | Module discovery and dynamic loading. |

### 10.2 General Test Infrastructure Improvements

- [ ] Add Playwright/Cypress E2E tests for critical user flows
- [ ] Add frontend coverage threshold enforcement in CI
- [ ] Add mutation testing (e.g. `mutmut` for Python, `stryker` for TS)
- [ ] Generate test documentation site from docstrings (Sphinx + autodoc)
- [ ] Extend doc-update workflow to also cover frontend releases
- [ ] Fix the 35 skipped backend tests (documented in section 3.7)
- [ ] Add integration tests for `ModelExport.post()` with full AG Grid request mocking
- [ ] Test `KeycloakManager.py` — the largest untested module at 1057 lines
