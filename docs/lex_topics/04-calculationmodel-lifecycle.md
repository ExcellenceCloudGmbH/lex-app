# CalculationModel Lifecycle

Search keywords: CalculationModel, is_calculated, status, calculate_hook, calculate, state machine, is_atomic

## Scope

- Status-driven computation model and state machine
- Hook-triggered execution behavior
- Error-state persistence and auto-save
- Atomic vs non-atomic calculations
- Celery integration and nested calculations

## Boundary rule

- `CalculationModel` is preimplemented by the Lex framework.
- Use it as a base class for project models; do not re-implement or alter framework internals.

## Import

```python
from lex.core.models.CalculationModel import CalculationModel
```

## The State Machine

Every `CalculationModel` has an `is_calculated` field with 5 states:

| State | Constant | Meaning |
|---|---|---|
| `NOT_CALCULATED` | `CalculationModel.NOT_CALCULATED` | Default — record exists but hasn't been processed |
| `IN_PROGRESS` | `CalculationModel.IN_PROGRESS` | Calculation running (triggers `calculate()`) |
| `SUCCESS` | `CalculationModel.SUCCESS` | Completed without error |
| `ERROR` | `CalculationModel.ERROR` | Exception raised — stored in `calculation_error_message` |
| `ABORTED` | `CalculationModel.ABORTED` | Manually cancelled |

Transitions: `NOT_CALCULATED → IN_PROGRESS → SUCCESS/ERROR`. Error/Aborted records can retry → `IN_PROGRESS`. Successful records can recalculate → `IN_PROGRESS`.

The `is_calculated` field is managed entirely by the framework — not editable in the UI.

## `calculate()` Method

Override this with your business logic. The framework handles everything else:

```python
class BudgetSummary(CalculationModel):
    team = models.ForeignKey('Team', on_delete=models.CASCADE)
    total_expenses = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    def calculate(self):
        expenses = Expense.objects.filter(employee__team=self.team)
        self.total_expenses = expenses.aggregate(
            total=models.Sum("amount")
        )["total"] or 0
```

### What You Don't Need to Write

| Concern | Handled By |
|---|---|
| `self.save()` | Framework saves automatically after `calculate()` |
| Error handling | Framework catches exceptions, sets `is_calculated = ERROR` |
| State transitions | Lifecycle hooks manage the flow |
| Logging context | LexLogger automatically links to current calculation |
| Concurrency | Runs inside `transaction.atomic()` by default |

> The legacy method name `update()` is also supported. Prefer `calculate()` for new code.

## Atomic vs Non-Atomic

By default, `calculate()` runs inside `transaction.atomic()`. For long-running calculations:

```python
class LargeImport(CalculationModel):
    is_atomic = False

    def calculate(self):
        # Commits happen incrementally — no rollback on failure
        ...
```

## Celery Integration

Calculations can be dispatched to Celery workers. Decorate with `@lex_shared_task`:

```python
from lex.lex_app.celery_tasks import lex_shared_task

class HeavyReport(CalculationModel):
    @lex_shared_task
    def calculate(self):
        ...
```

Two conditions must be true for async dispatch:
1. `CELERY_ACTIVE=true` in environment
2. `calculate()` decorated with `@lex_shared_task`

Without both, calculation runs synchronously. See `12-celery-async-dispatch.md`.

## Nested Calculations

When a parent triggers a child, wrap in `model_logging_context`:

```python
from lex.audit_logging.utils.ModelContext import model_logging_context

class ParentReport(CalculationModel):
    def calculate(self):
        child = ChildReport.objects.get(pk=self.child_id)
        with model_logging_context(child):
            child.is_calculated = "IN_PROGRESS"
            child.save()
```

## Inherited Features

Since `CalculationModel` extends `LexModel`, you also get:
- `created_by` / `edited_by` tracking
- `pre_validation()` / `post_validation()` hooks
- All `permission_*()` methods

## Where to Expand

- `lex_context.md`: CalculationModel — Models That Compute on Save
- `lex_context_repo.md`: CalculationModel — Status-Tracked Calculations; Celery Integration
- `docs/_context/lex_examples/CalculationModelExplain.py`: concrete implementation reference

## LLM Prompt Starters

- "Design a project model that subclasses `CalculationModel` and transitions statuses correctly with failure logging."
- "Design subclass `calculate` logic with Celery fallback, preserving `IN_PROGRESS -> SUCCESS/ERROR` flow."
