# LEX Framework — Test Plan

> **Audience:** Engineering leadership, QA supervisors, developers  
> **Status:** Living document — updated as clusters are implemented  
> **Last updated:** April 17, 2026

---

## Executive Summary

We are shifting from **implementation-coupled unit tests** to **behavior-driven integration and E2E tests** built around a dedicated test project that mirrors real customer usage.

The old approach gave us **2,000+ passing tests and 60% coverage** — but missed real production bugs because tests were tightly coupled to internal APIs rather than testing what the framework *actually does*.

The new approach tests the framework **the way a customer uses it**: create models, save data, trigger calculations, check history, verify permissions — through the real ORM and REST API paths.

> ### ⚠️ The Golden Rule
>
> **Test what the framework is _trying to achieve_, not what the current code happens to do.**
>
> The source code is an **incomplete story**. It has bugs, shortcuts, and workarounds. Tests must derive their expectations from **documented intent** and **reasonable customer expectations** — not from reading the current implementation.
>
> If the code does the wrong thing, the test should fail. That failure is the test doing its job. See [full rules in testing-philosophy.md](testing-philosophy.md#the-rules).

---

## What Changed

| Aspect | Old Approach | New Approach |
|--------|-------------|--------------|
| **Test target** | Internal methods, private APIs | Observable behavior through public interfaces |
| **Mocking strategy** | Mock the class under test, mock the ORM | Mock only at true boundaries (Celery, WebSocket, S3) |
| **Model setup** | Fake stubs (`_make_model_stub()`) | Real Django models with real DB tables |
| **Confidence level** | Tests pass ≠ framework works | Tests pass = feature works as documented |
| **Bug detection** | Missed real bugs, caught refactoring noise | Catches behavioral regressions, survives refactors |
| **Test project** | None — tests scattered across framework internals | Dedicated `test_project/` mimicking customer structure |

---

## Why This Shift Was Necessary

**→ [Full explanation with evidence](why-the-shift.md)**

In short: we found tests that were **100% green but tested nothing real**. One test file (`test_calculation_signals.py`) mocked an API that had been *removed from production code* — the tests kept passing silently. Another test suite (`test_lifecycle_hooks.py`) used fake model stubs instead of real Django models — it verified a list of method names, not that hooks actually fire.

Meanwhile, when we switched our E2E tests to use the **documented canonical pattern** (`instance.save()` instead of `save(skip_hooks=True)` + `calculate_hook()`), we immediately discovered a real framework bug: `LexModel.save()` wraps the IN_PROGRESS state write and all hooks in a single `transaction.atomic()`, so when a calculation fails, the rollback erases the IN_PROGRESS history record — losing forensic evidence of what happened.

**Old tests:** 2,000+ green ✓, bug undetected  
**New tests:** 9 red ✗, bug found and documented

---

## Test Project Structure

All tests are built around a **dedicated test project** at `lex/test_project/` that mirrors how real customers use the framework. Each cluster lives in its own folder under `tests/` and owns the models it needs — **`models.py` lives inside the cluster folder**, not in a central `models/` directory.

```
lex/test_project/
├── lex_config.py              # Project configuration
├── app.py                     # Django AppConfig
├── __init__.py
└── tests/                     # One folder per cluster, named after the cluster slug
    ├── __init__.py
    ├── fixtures/              # Shared JSON seed fixtures
    ├── init/                  # Cluster 1 — Project bootstrap
    │   ├── models.py
    │   └── test_1a_lex_setup.py, test_1b_..., ...
    ├── crud_api/              # Cluster 2 — CRUD via REST API
    │   ├── models.py          # SimpleItem, TrackedItem, ...
    │   └── test_2a_create.py, test_2b_read.py, ...
    ├── validation_hooks/      # Cluster 3 — Validation hooks
    │   ├── models.py
    │   └── test_3*.py
    ├── permissions/           # Cluster 4 — Permissions
    │   ├── models.py
    │   └── test_4*.py
    ├── history/               # Cluster 5 — History & bitemporal
    │   ├── models.py
    │   └── test_5*.py
    ├── audit_logging/         # Cluster 6 — Audit logging
    │   ├── models.py
    │   └── test_6*.py
    ├── calculations/          # Cluster 7 — Calculation state machine
    │   ├── models.py
    │   └── test_7a_atomic.py, test_7b_non_atomic.py, ...
    ├── calculation_logging/   # Cluster 15 — Calculation logging surface
    ├── celery_async/          # Cluster 8 — Celery & async
    ├── signals_ws/            # Cluster 9 — Signals & WebSocket
    ├── api_layer/             # Cluster 10 — API layer
    ├── serializers/           # Cluster 12 — Serializers
    ├── queries/               # Cluster 14 — AG Grid queries
    ├── exports/               # Exports surface
    ├── journeys/              # End-to-end user journeys
    └── stress/                # Stress / load harness
```

Each cluster folder name is also its pytest group. The marker is declared
once per test module via `pytestmark = pytest.mark.<group>`; the canonical
group list lives in `lex_test_config.yaml` at the repo root.

**Naming convention:** test files inside a cluster folder follow `test_<Nx>_<slug>.py` where `Nx` is the cluster number + sub-cluster letter (e.g. `2a`, `7g`). The PR-gate workflow enforces this regex on Copilot-authored test files.

---

## Test Clusters

**→ [Detailed cluster breakdown — one folder per cluster under `clusters/`](clusters/)** (each `clusters/NN-<slug>/cluster.md` owns its scope + scenarios; philosophy + Golden Rule live in [`testing-philosophy.md`](testing-philosophy.md))

| # | Cluster | What It Tests | Key Risk It Covers |
|---|---------|---------------|-------------------|
| 1 | [Init — Project Bootstrap](clusters/01-init/cluster.md) | `lex setup`, `lex Init`, Keycloak sync, seed data loading | Customer can't start, broken onboarding |
| 2 | [CRUD via REST API](clusters/02-crud_api/cluster.md) | POST / GET / PATCH / DELETE through the real HTTP API | Data corruption, wrong status codes, broken serializers, actor tracking failures |
| 3 | [Validation Hooks](clusters/03-validation_hooks/cluster.md) | pre_validation (cancel), post_validation (rollback) | Invalid data persisted, failed rollbacks |
| 4 | [Permissions](clusters/04-permissions/cluster.md) | Field-level and action-level access control | Data leaks, unauthorized modifications |
| 5 | [History & Bitemporal](clusters/05-history/cluster.md) | History rows on every save, valid_from/valid_to chaining | Missing audit trail, lost change evidence |
| 6 | [Audit Logging](clusters/06-audit_logging/cluster.md) | AuditLogMixin lifecycle, calculation audit finalization | Missing compliance records, orphaned pending audits |
| 7 | [Calculation State Machine](clusters/07-calculations/cluster.md) | State transitions, atomic/non-atomic, parent→child chains | Stuck IN_PROGRESS, lost errors, silent failures |
| 8 | [Celery & Async](clusters/08-celery_async/cluster.md) | Task dispatch, sync fallback, FireAndForget/WaitForTasks | Silent task failures, lost calculation results |
| 9 | [Signals & WebSocket](clusters/09-signals_ws/cluster.md) | State store, WebSocket notifications, cache cleanup | Phantom spinners, stale cache, orphaned state |
| 10 | [API Layer](clusters/10-api_layer/cluster.md) | REST endpoints (CRUD, history, bulk), serializers | Broken API contracts, wrong HTTP status codes |
| 11 | [Stress & Performance](clusters/11-stress/cluster.md) | ~20k-row workloads: list/export/period-calc runtime, query counts, memory, N+1 detection | Algorithmic drift, runaway exports, period calcs that time out on real data |
| 12 | [Serializer Contract](clusters/12-serializers/cluster.md) | JSON shape the API hands the frontend — field visibility, type round-trip (Decimal / DateTime / FK), `lex_reserved_scopes`, AuditLog payload filtering | Silent UI breakage when a serializer change drops a key, loses decimal precision, or strips a timezone |
| 13 | [Export Endpoint](clusters/13-exports/cluster.md) | Real `POST /api/<model>/export` — legacy path, AG Grid flat/grouped, row selection, FK display names, field masking | "Export to Excel" returning wrong columns, broken groupings, or FKs rendered as integer pks instead of readable names |
| 14 | [AG Grid Query Endpoint](clusters/14-queries/cluster.md) | `GET/POST /api/model_entries/<model>/list` — query-param filters, AG Grid filterModel / sortModel, grouping, aggregation, pivot | Silent query mistranslation (wrong rows, empty results, wrong sort order) in every grid the user opens |
| 15 | [Calculation Logging Surface](clusters/15-calculation_logging/cluster.md) | `LexLogger` builder + `model_logging_context` → `CalculationLog` rows: content shape, parent/child hierarchy, silent children, three-level chains, combinatorial fan-out, `.save()`-boundary preservation | Lost or misattributed calculation logs, collapsed child log rows |

> **Ordering rationale:** Clusters follow the customer journey — setup → data management → data quality → access control → compliance → processing → scaling → real-time → external API. [Read more](testing-philosophy.md#ordering-the-user-journey)

---

## Expected Results

**→ [Detailed outcomes and KPIs](expected-results.md)**

| Metric | Before (Old Tests) | Target (New Tests) |
|--------|--------------------|--------------------|
| Tests that catch real bugs | ~30% (estimated) | >90% |
| False confidence rate | High (green tests, hidden bugs) | Near zero |
| Tests surviving a refactor | ~50% break on rename | >95% survive |
| Coverage (meaningful) | 60% line coverage | 70%+ behavior coverage |
| Known bugs detected by tests | 0 (found manually) | All known bugs have a failing test |

---

## Navigation

- **[Why the Shift Was Necessary](why-the-shift.md)** — Evidence-based explanation with concrete examples
- **[Testing Philosophy](testing-philosophy.md)** — Golden Rule, ordering rationale, red flags
- **[Test Clusters](clusters/)** — one folder per cluster (`clusters/NN-<slug>/cluster.md`)
- **[Expected Results](expected-results.md)** — KPIs, success criteria, and timeline
- **[Progress & Organization](progress.md)** — How we measure progress, track quality, and stay organized
- **[Cleanup & Coverage Completion Plan](cleanup-and-coverage-plan.md)** — May 2026 supervisor categorisation (EXCLUDE / DELETE / COMPLETE / LATER) with dependency triage and cluster mapping
