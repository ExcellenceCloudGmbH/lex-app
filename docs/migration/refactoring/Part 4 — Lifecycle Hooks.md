---
title: "Part 4 — Lifecycle Hooks"
---

V1 used `UploadModelMixin` to trigger processing when a record was created. The mechanism was implicit — you defined an `update()` method and it ran automatically. The current framework uses explicit `@hook` decorators, which makes it clear exactly when and why a method runs.

## What Changes

| Aspect | V1 (`UploadModelMixin`) | Current (`@hook`) |
|---|---|---|
| Base class | `UploadModelMixin` | `LexModel` |
| Trigger | Implicit (hidden in mixin) | Explicit `@hook(AFTER_CREATE)` decorator |
| Method name | `update()` | Any descriptive name you choose |
| When it runs | Hidden — hard to trace | Declared on the decorator |

## Step-by-Step Conversion

### 1. Change the Base Class

```python
# Before
from generic_app.generic_models.upload_model import UploadModelMixin

class UploadBalanceSheet(UploadModelMixin):
    pass
```

```python
# After
from lex.core.models.LexModel import LexModel
from django_lifecycle import hook, AFTER_CREATE

class UploadBalanceSheet(LexModel):
    pass
```

### 2. Add the `@hook` Decorator

Replace the implicit `update()` method with an explicitly decorated method:

```python
# Before
class UploadBalanceSheet(UploadModelMixin):
    file = models.FileField(upload_to='uploads/')

    def update(self):
        df = pd.read_excel(self.file.path)
        for _, row in df.iterrows():
            BalanceSheetEntry.objects.create(
                account_name=row['Account'],
                amount=row['Amount']
            )
        self.processed_rows = len(df)
        self.save()
```

```python
# After
class UploadBalanceSheet(LexModel):
    file = models.FileField(upload_to='uploads/')
    processed_rows = models.IntegerField(default=0)

    @hook(AFTER_CREATE)
    def process_file(self):
        df = pd.read_excel(self.file.path)
        for _, row in df.iterrows():
            BalanceSheetEntry.objects.create(
                account_name=row['Account'],
                amount=row['Amount']
            )
        self.processed_rows = len(df)
        self.save(skip_hooks=True)
```

### 3. Use `skip_hooks=True` When Saving Inside Hooks

> [!warning]
> If you call `self.save()` inside a hook without `skip_hooks=True`, you'll get infinite recursion. Always use `self.save(skip_hooks=True)`.

### 4. Choose the Right Hook Type

V1's `update()` always ran after create. With explicit hooks, you can choose exactly when to trigger:

| Hook | When It Fires |
|---|---|
| `AFTER_CREATE` | After the first `save()` — closest to V1 behavior |
| `AFTER_UPDATE` | After subsequent saves |
| `AFTER_SAVE` | After any save (create or update) |
| `BEFORE_SAVE` | Before any save |
| `BEFORE_DELETE` | Before deletion |

Most V1 `update()` methods should become `@hook(AFTER_CREATE)`.

## Upload + Calculate Pattern

Some V1 models used `UploadModelMixin` for file uploads where the user also clicked "Calculate". If your model needs both a file upload and a calculate button, use `CalculationModel` instead:

```python
# If the model has a Calculate button
from lex.core.models.CalculationModel import CalculationModel

class UploadBalanceSheet(CalculationModel):
    file = models.FileField(upload_to='uploads/')

    def calculate(self):
        df = pd.read_excel(self.file.path)
        # ... processing logic ...
```

Use `LexModel` + `@hook(AFTER_CREATE)` only when processing should happen automatically on upload with no user interaction.

## Adding Conditions

The current framework lets you add conditions to hooks — something V1 couldn't do at all:

```python
from django_lifecycle.conditions import WhenFieldHasChanged

class Invoice(LexModel):
    status = models.CharField(max_length=50)

    @hook(AFTER_UPDATE, condition=WhenFieldHasChanged("status"))
    def notify_status_change(self):
        # Only runs when the status field actually changed
        NotificationService.send(f"Invoice status changed to {self.status}")
```

For more on hooks, conditions, and the validation system, see [[features/data-pipeline/lifecycle hooks]].

## Checkpoint

- [ ] All `UploadModelMixin` classes converted to `LexModel`
- [ ] `update()` methods replaced with descriptive names + `@hook(AFTER_CREATE)`
- [ ] All `self.save()` calls inside hooks use `skip_hooks=True`
- [ ] Upload+Calculate models use `CalculationModel` instead

Next: [[migration/refactoring/Part 5 — Logging & Permissions|Part 5 — Logging & Permissions]].
