# Calculation Engine Tests — `lex.tests.unit.calculation`

> **Story:** *"When a user saves a model instance, the system must decide
> whether to calculate synchronously, dispatch to Celery, or skip entirely —
> and it must track every state transition along the way."*

## What Lives Here (11 files)

| File | Tests | Covers |
|------|------:|--------|
| `test_calculation_model_state_machine.py` | 33 | Status constants, `CalculationStatus` resolution, state-machine transitions, Celery detection, error persistence, and `CalculationInfo` construction |
| `test_calculate_hook.py` | 17 | The `calculate` lifecycle hook — sync path, Celery path, re-entrancy guard, and error-state persistence |
| `test_dispatch_calculation_task.py` | 5 | Celery task dispatch: operation-context extraction, `model_context` propagation, and `LexCalculationTask` registration |
| `test_calculation_signals.py` | 18 | Status broadcasts (IN_PROGRESS / SUCCESS / ERROR / ABORTED), `WebSocketNotifier` sync, and calculation-ID resolution priority chain |
| `test_active_calculation_state_store.py` | 29 | In-memory calculation tracker: mark / clear / snapshot API, record-ID parsing, startup pruning, and model resolution |
| `test_calculated_model_mixin.py` | 7 | Lazy iterable materialisation, Cartesian product generation, and `should_calculate` / dispatch context handling |
| `test_calculation_history_transitions.py` | 4 | History-row recording during `CalculationStatus` transitions through API-driven save/calculate cycles |
| `test_calculation_wait_contexts.py` | 4 | `WaitForCalculation` / `CheckCalculation` context behaviour: local waiting, nested reuse, sync-inside-worker fallback |
| `test_celery_task_dispatcher.py` | 25 | Group dispatch, single-group dispatch, sync fallback on Celery failure, task-result handling (success / partial-failure / retry) |
| `test_objects_to_recalculate_store.py` | — | ObjectsToRecalculateStore — mark/clear/query objects pending recalculation |
| `test_operation_context.py` | — | OperationContext — thread-local actor/calculation-ID propagation |

## Key Concepts Tested

- **State machine** — valid transitions between `NOT_CALCULATED → IN_PROGRESS → SUCCESS / ERROR`
- **Dispatch routing** — sync when Celery is down, Celery when a broker is available
- **Broadcast** — `CalculationSignals` fans out status changes to WebSocket + audit
- **Concurrency** — `ActiveCalculationStateStore` prevents duplicate in-flight calculations
- **Wait contexts** — callers can block until a calculation finishes (or check without blocking)

## How to Run

```bash
source ~/LUND_IT/ArmiraCashflowDB/.venv/bin/activate
lex test lex.tests.unit.calculation          # all tests
lex test lex.tests.unit.calculation.test_calculation_model_state_machine  # 33 tests
```
