# Unit Tests — `lex.tests.unit`

> **~1 850 tests** across **125 files** in **11 topic directories**

These are pure unit tests — no database, no network, no Celery broker.
Every test mocks external dependencies and runs in < 40 s total.

## Directory Map

| Directory | What it covers | Files | Tests |
|-----------|---------------|------:|------:|
| [api/](api/) | REST views, serializers, model structure, permissions | 28 | ~350 |
| [audit/](audit/) | Audit-log mixins, context resolution, cache, WebSocket | 21 | ~300 |
| [auth/](auth/) | Keycloak middleware, permissions, user context, tokens | 9 | ~180 |
| [calculation/](calculation/) | State machine, hooks, signals, Celery dispatch | 11 | ~170 |
| [cli/](cli/) | `lex` CLI — argument parsing, project-root discovery | 1 | ~40 |
| [core/](core/) | LexModel base, lifecycle hooks, combinations, exceptions | 12 | ~218 |
| [crud/](crud/) | Generic filters, API helpers | 2 | ~40 |
| [grid/](grid/) | AG Grid utilities, export, PK/user-read filter backends | 7 | ~158 |
| [infra/](infra/) | Celery callbacks, health check, Keycloak timeout, runtime config | 20 | ~100 |
| [serialization/](serialization/) | LexSerializer, permission-aware mixin, parse_value | 4 | ~80 |
| [temporal/](temporal/) | Bitemporal parsing, sync, suppression, reconciliation | 10 | ~100 |

## How to Run

```bash
source ~/LUND_IT/ArmiraCashflowDB/.venv/bin/activate

# All unit tests
lex test lex.tests.unit

# One topic
lex test lex.tests.unit.calculation

# One file
lex test lex.tests.unit.calculation.test_calculation_model_state_machine

# One class
lex test lex.tests.unit.calculation.test_calculation_model_state_machine.CalculationModelStateMachineTest
```

## Backward Compatibility

Every old path (`lex.core.tests.test_X`, `lex.audit_logging.tests.test_Y`, …)
still works — each original file is now a 2-line re-export shim that imports
from the canonical location here.

## Design Principles

| Principle | How |
|-----------|-----|
| **Zero I/O** | Every external call is mocked (`unittest.mock.patch`) |
| **Fast feedback** | Full unit suite runs in ~40 s |
| **One concept per file** | Each file tests exactly one source module/class |
| **Descriptive names** | Test methods read as specifications: `test_mark_in_progress_sets_status` |
| **Topic clustering** | Files grouped by domain concept, not by source package |
