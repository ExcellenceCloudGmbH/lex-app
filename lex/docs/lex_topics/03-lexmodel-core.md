# LexModel Core

Search keywords: LexModel, lifecycle hooks, pre_validation, post_validation, rollback, history, created_by, edited_by

## Scope

- Base model responsibilities and auto-provided fields
- Built-in metadata fields and manager behavior
- Validation hooks and rollback mechanisms
- History tracking control
- Permission method API summary

## Boundary rule

- `LexModel` is preimplemented by the Lex framework.
- Use it as a base class and extension point only.
- Do not re-implement, modify, or document internal Lex framework class definitions in planning artifacts.

## Import

```python
from lex.core.models.LexModel import LexModel, UserContext, PermissionResult
```

## Auto-Provided Fields

Every `LexModel` subclass automatically gets (never declare these yourself):

| Field | Type | Description |
|---|---|---|
| `id` | `AutoField` | Primary key (inherited from Django) |
| `created_by` | `TextField` | Username of the creator (set automatically) |
| `edited_by` | `TextField` | Username of the last editor (set automatically) |

## Validation Hooks

### `pre_validation(self)`

Called **before** every save. Raise any exception to cancel the save entirely:

```python
def pre_validation(self):
    if self.amount <= 0:
        raise ValueError("Amount must be positive.")
```

### `post_validation(self)`

Called **after** save completes. Raise any exception to trigger automatic rollback to pre-save state:

```python
def post_validation(self):
    total = ExpenseReport.objects.filter(
        quarter=self.quarter
    ).aggregate(total=models.Sum('amount'))['total'] or 0
    if total > self.quarter.expense_budget:
        raise ValueError("This would exceed the team budget.")
```

> Both hooks are wired internally via django-lifecycle. Override the method — no decorator needed.

## Permission Methods

### Field-Level (return `PermissionResult`)

| Method | Default | Called On |
|---|---|---|
| `permission_read(user_context)` | Allow if Keycloak `read` scope | GET / list |
| `permission_edit(user_context)` | Allow if Keycloak `edit` scope | PATCH / PUT |
| `permission_export(user_context)` | Allow if Keycloak `export` scope | CSV/Excel export |

### Action-Level (return `bool`)

| Method | Default | Called On |
|---|---|---|
| `permission_create(user_context)` | Allow if Keycloak `create` scope | POST |
| `permission_delete(user_context)` | Allow if Keycloak `delete` scope | DELETE |
| `permission_list(user_context)` | Allow if Keycloak `list` scope | Table view |

See `06-permissions-authorization.md` for full `UserContext`, `PermissionResult`, and code examples.

## History Tracking Control

| Method | What It Does |
|---|---|
| `track()` | Re-enable history tracking for this instance |
| `untrack()` | Disable history tracking for the next save |
| `save_without_historical_record()` | Save once without creating a history entry |

For bulk operations: `Model.objects.bulk_create(objs, skip_history=True)`.

## Minimal Model Example

```python
from lex.core.models.LexModel import LexModel
from django.db import models

class Fund(LexModel):
    name = models.CharField(max_length=200)
    department = models.CharField(max_length=200)
    budget = models.DecimalField(max_digits=12, decimal_places=2)

    def pre_validation(self):
        if self.budget < 0:
            raise ValueError("Budget cannot be negative.")
```

## Where to Expand

- `lex_context.md`: LexModel sections; Validation Hooks; History Tracking
- `lex_context_repo.md`: Models — LexModel Base Class; Validation Hooks; History Tracking

## LLM Prompt Starters

- "Design a domain model that subclasses `LexModel` with pre/post validation hooks."
- "Show how to apply history-safe save patterns in a `LexModel` subclass."
