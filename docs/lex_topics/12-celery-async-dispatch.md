# Celery Async Dispatch

Search keywords: celery_active, async dispatch, RunInCelery, UnblockCelery, lex_shared_task, task fallback, Redis, Memurai

## Scope

- Optional async execution path for calculations
- `@lex_shared_task` decorator
- Environment variables and worker setup
- `RunInCelery` / `UnblockCelery` context managers
- Failure handling and fallback guarantees

## Key Points

- Celery is optional. Without it, all calculations run synchronously — your code doesn't change.
- Enable with `CELERY_ACTIVE=true` in `.env` + `@lex_shared_task` on `calculate()`.
- Redis (or Memurai on Windows) is required as message broker.
- Multi-level failure handling: individual task failures retry synchronously, broker failures fall back entirely.

## The `@lex_shared_task` Decorator

```python
from lex.lex_app.celery_tasks import lex_shared_task

class HeavyReport(CalculationModel):
    @lex_shared_task
    def calculate(self):
        ...
```

Wraps the method with:
- Context-aware dispatch (respects `RunInCelery`/`UnblockCelery`)
- Automatic status callbacks (`SUCCESS`/`ERROR` on completion)
- Context propagation (calculation IDs, audit logging forwarded to workers)

Without the decorator, `calculate()` always runs synchronously.

## Environment Variables

| Variable | Where to Set | Purpose |
|---|---|---|
| `CELERY_ACTIVE=true` | `.env` (app **and** workers) | Enables Celery dispatch path |
| `IS_RUNNING_IN_CELERY=true` | Worker command only | Tells framework process is a worker (skips startup tasks). **Do not** set in main `.env`. |

## Running Workers

### Linux / macOS
```bash
IS_RUNNING_IN_CELERY=true CELERY_ACTIVE=true lex celery -A lex_app worker \
  --loglevel=info \
  --concurrency=12 \
  --prefetch-multiplier=1 \
  -n worker1@%h
```

| Flag | Meaning |
|---|---|
| `--concurrency=12` | Parallel worker threads/processes |
| `--prefetch-multiplier=1` | Don't prefetch — important for long-running calculations |
| `-n worker1@%h` | Worker name. Use `worker2@%h` etc. for additional workers |

### Windows
Use `--pool=solo` or `--pool=threads` (prefork not supported), or run via WSL.

## `RunInCelery` and `UnblockCelery`

### `RunInCelery`
Forces `@lex_shared_task` functions to dispatch to Celery:

```python
from lex.lex_app.celery_tasks import RunInCelery

with RunInCelery():
    my_task(data)  # dispatched to Celery

# Selective dispatch:
with RunInCelery(include_tasks={"calc_and_save"}):
    ...
with RunInCelery(exclude_tasks={"initial_data_upload"}):
    ...
```

### `UnblockCelery`
Nested inside `RunInCelery`, overrides blocking and forces async:

```python
from lex.lex_app.celery_tasks import RunInCelery, UnblockCelery

with RunInCelery():
    my_task(data)        # dispatched, waited on
    with UnblockCelery():
        my_task(data)    # dispatched, NOT waited on
```

Priority: `UnblockCelery` > `RunInCelery` > default (sync).

## Failure Handling

| Failure Type | What Happens |
|---|---|
| Celery import fails | Entire batch runs synchronously |
| Single task dispatch fails | That group runs sync, others continue on Celery |
| Task execution fails | Failed group retried synchronously |
| Broker goes down | Remaining groups run synchronously |

## Monitoring

```bash
lex celery -A lex_app flower
```

Web dashboard at `http://localhost:5555` with real-time task monitoring.

## When to Use Celery

| Scenario | Useful? |
|---|---|
| Single calculation, no children | No |
| Parent triggers 10+ children | Yes |
| Long-running calculations (minutes+) | Yes |
| Development / small projects | No |

## Where to Expand

- `lex_context.md`: Celery Integration (Async Calculations)
- `lex_context_repo.md`: Celery Integration — Async Task Dispatch

## LLM Prompt Starters

- "Implement this calculation flow so it runs async with Celery and falls back to sync safely."
- "Diagnose why Celery dispatch is skipped and list config/runtime checks in order."
