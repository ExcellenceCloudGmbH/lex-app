# Core Model Tests — `lex.tests.unit.core`

> **Story:** *"LexModel is the base class every domain model inherits from.
> It must handle atomic saves, lifecycle hooks, field validation, combination
> generation, and programmatic CRUD — all before any calculation or audit
> concern kicks in."*

## What Lives Here (12 files, 218 tests)

| File | Tests | Covers |
|------|------:|--------|
| `test_lex_model_core.py` | 38 | Core `LexModel` — datetime helpers, atomic flag, Keycloak-based default permissions, save flow with timestamps/actors, `valid_from`, `sys_from`, field introspection |
| `test_lexmodel_atomic_save.py` | 3 | `atomic_save` wraps lifecycle hooks in `transaction.atomic`; `non_atomic_save` skips it |
| `test_lifecycle_hooks.py` | 12 | Hook dispatch order (BEFORE_CREATE → AFTER_CREATE etc.), pre/post validation with rollback, `on_create` / `on_update`, snapshot/restore |
| `test_model_validation.py` | 14 | Rejects reserved names (History, MetaHistory, LexModel), reserved prefixes (Historical*, Meta*), reserved field names (valid_from, sys_to, etc.) |
| `test_combination_engine.py` | 31 | Field-value normalisation, Cartesian product generation, field overrides, cluster grouping by parallelisable fields, cluster flattening |
| `test_combination_and_cluster.py` | 34 | Extended combination/cluster tests — error classes (`CombinationError`, `ClusterError`), `CombinationRunner` with partial/full failure handling |
| `test_create_flow_and_duplicates.py` | 17 | End-to-end `create` pipeline, duplicate detection, idempotent update, `Combination` ↔ `CreateFlow` resolution, model-preparation validation |
| `test_exceptions.py` | 46 | Pure-logic exception helpers — chain traversal, message normalisation, preferred-detail selection, artifact discovery, and construction of all framework exception types |
| `test_programmatic_creation.py` | 16 | Programmatic `create`, `update`, `delete`, `bulk_create` — all trigger the full bitemporal pipeline (History + MetaHistory + main-table sync) |
| `test_reconcile_command.py` | 1 | `reconcile_time` management command activates stale future-valid history records |
| `test_future_activation_scheduler_routing.py` | 2 | `FutureActivationScheduler` routes to `LocalScheduler` when Celery is inactive, Celery Beat when active |
| `test_local_scheduler.py` | 4 | Singleton initialisation, task scheduling with `apscheduler`, immediate-execution edge case, worker-thread run loop |

## Key Concepts Tested

- **LexModel lifecycle** — `BEFORE_CREATE → validate → save → AFTER_CREATE` hook chain
- **Atomic saves** — `transaction.atomic` wrapping with opt-out for bulk operations
- **Combination engine** — Cartesian product generation from field sets for scenario modelling
- **Duplicate detection** — idempotent create-or-update based on unique-together constraints
- **Exception taxonomy** — structured error types (`LexError`, `ValidationError`, `CombinationError`, …)
- **Temporal scheduling** — future-valid records activated by `reconcile_time` command or scheduler

## How to Run

```bash
source /path/to/your-project/.venv/bin/activate  # the host project where lex-app is installed editable
lex test lex.tests.unit.core               # all 218 tests
lex test lex.tests.unit.core.test_exceptions  # 46 tests
```
