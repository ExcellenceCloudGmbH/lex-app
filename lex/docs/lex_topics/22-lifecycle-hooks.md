# Lifecycle Hooks

Search keywords: hook, AFTER_CREATE, BEFORE_UPDATE, django-lifecycle, skip_hooks, pre_validation, post_validation, rollback

## Scope

- Reacting to model events with `@hook` decorators
- Available hook types and conditional hooks
- Validation hooks with auto-rollback
- Recursion prevention with `skip_hooks=True`

## Key Points

- Lex models support lifecycle hooks via `@hook` decorator from django-lifecycle.
- Hooks run automatically at specific points in a model's lifecycle (create, update, delete).
- `pre_validation()` blocks invalid data before save; `post_validation()` rolls back after save.
- Always use `skip_hooks=True` when calling `save()` inside a hook to prevent infinite recursion.

## Import

```python
from django_lifecycle import hook, AFTER_CREATE, BEFORE_UPDATE, AFTER_UPDATE, AFTER_SAVE
from django_lifecycle.conditions import WhenFieldValueIs, WhenFieldHasChanged
```

## Basic Example

```python
from django_lifecycle import hook, AFTER_CREATE
from lex.core.models.LexModel import LexModel
from django.db import models

class UploadBalanceSheet(LexModel):
    quarter = models.ForeignKey('Quarter', on_delete=models.CASCADE)
    balance_sheet_file = models.FileField(upload_to='balance_sheets/')
    processed_rows = models.IntegerField(default=0)

    @hook(AFTER_CREATE)
    def process_file(self):
        import pandas as pd
        df = pd.read_excel(self.balance_sheet_file.path)

        for _, row in df.iterrows():
            BalanceSheetEntry.objects.create(
                quarter=self.quarter,
                account_name=row['Account'],
                amount=row['Amount']
            )

        self.processed_rows = len(df)
        self.save(skip_hooks=True)
```

## Available Hooks

| Hook | When It Fires |
|---|---|
| `BEFORE_CREATE` | Before the first `save()` (new record) |
| `AFTER_CREATE` | After the first `save()` (new record) |
| `BEFORE_UPDATE` | Before subsequent `save()` calls |
| `AFTER_UPDATE` | After subsequent `save()` calls |
| `BEFORE_SAVE` | Before any `save()` (create or update) |
| `AFTER_SAVE` | After any `save()` (create or update) |
| `BEFORE_DELETE` | Before `delete()` |
| `AFTER_DELETE` | After `delete()` |

## Conditional Hooks

```python
from django_lifecycle import hook, AFTER_UPDATE
from django_lifecycle.conditions import WhenFieldValueIs, WhenFieldHasChanged

class Invoice(LexModel):
    status = models.CharField(max_length=50)
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    @hook(AFTER_UPDATE, condition=WhenFieldValueIs("status", "Paid"))
    def send_receipt(self):
        EmailService.send_receipt(self)

    @hook(AFTER_UPDATE, condition=WhenFieldHasChanged("amount"))
    def log_amount_change(self):
        LexLogger().add_text(f"Amount changed to {self.amount}").log()
```

## Preventing Recursion

When calling `save()` inside a hook, always use `skip_hooks=True`:

```python
@hook(AFTER_CREATE)
def process_and_save(self):
    self.status = "Done"
    self.save(skip_hooks=True)  # prevents infinite recursion
```

## Validation Hooks

### `pre_validation()` — Guard Before Save

Raise any exception to cancel the save entirely:

```python
class Invoice(LexModel):
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    due_date = models.DateField()

    def pre_validation(self):
        if self.amount < 0:
            raise ValueError("Invoice amount cannot be negative.")
        if not self.pk and self.due_date < timezone.now().date():
            raise ValueError("Due date must be in the future for new invoices.")
```

### `post_validation()` — Verify After Save + Auto-Rollback

Checks that need the saved state (e.g., aggregate constraints). Raising an exception triggers automatic rollback to pre-save state:

```python
class ExpenseReport(LexModel):
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    quarter = models.ForeignKey('Quarter', on_delete=models.CASCADE)

    def post_validation(self):
        total = ExpenseReport.objects.filter(
            quarter=self.quarter
        ).aggregate(total=models.Sum('amount'))['total'] or 0

        if total > self.quarter.expense_budget:
            raise ValueError(
                f"Total expenses ({total}) exceed quarterly budget "
                f"({self.quarter.expense_budget}). This expense was rolled back."
            )
```

## Validation vs. Serializer Validation

| Use Case | Pattern |
|---|---|
| Universal rule (applies on every save) | `pre_validation()` on model |
| API-specific rule (formatting, PATCH checks) | Serializer `validate()` method |
| Aggregate constraint needing saved state | `post_validation()` on model |

## Which Pattern to Use?

| Use Case | Pattern |
|---|---|
| User-initiated calculation with progress tracking | `CalculationModel` + `calculate()` |
| Automatic processing on create (fire-and-forget) | `LexModel` + `@hook(AFTER_CREATE)` |
| Block invalid data before save | `LexModel` + `pre_validation()` |
| Verify constraints after save (with rollback) | `LexModel` + `post_validation()` |
| Side effects after save (logging, notifications) | `LexModel` + `@hook(AFTER_SAVE)` |

## Where to Expand

- `lex_context.md`: LexModel sections; Validation Hooks
- `lex_context_repo.md`: Models — LexModel Base Class; Validation Hooks

## LLM Prompt Starters

- "Add an AFTER_CREATE hook to this upload model that processes the uploaded file and creates child records."
- "Implement pre_validation and post_validation on this model with proper rollback semantics."
