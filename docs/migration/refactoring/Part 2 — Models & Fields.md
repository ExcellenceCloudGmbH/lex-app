---
title: "Part 2 — Models & Fields"
---

Now that your imports are updated, it's time to convert your model base classes and clean up legacy fields. This is where V1 models start to look like current Lex App models.

## Change Base Classes

Every V1 model inherits from `generic_app` base classes. These all map to either `LexModel` or `CalculationModel`:

| V1 Base Class | Current Base Class | When to Use |
|---|---|---|
| `Model` (from `generic_app`) | `LexModel` | Standard data models |
| `UploadModelMixin` | `LexModel` + `@hook` | Models that process on create (covered in [[migration/refactoring/Part 4 — Lifecycle Hooks\|Part 4]]) |
| `ConditionalUpdateMixin` | `CalculationModel` | Models with a calculate button (covered in [[migration/refactoring/Part 3 — Calculations\|Part 3]]) |

### Standard Model Migration

```python
# Before
from generic_app.models import *

class Fund(Model):
    name = models.CharField(max_length=200)
    vintage_year = models.IntegerField()
```

```python
# After
from django.db import models
from lex.core.models.LexModel import LexModel

class Fund(LexModel):
    name = models.CharField(max_length=200)
    vintage_year = models.IntegerField()
```

That's it for standard models. `LexModel` gives you everything `Model` did, plus bitemporal history, `created_by`/`edited_by` tracking, and the permission hooks.

## Remove Legacy Fields

V1 models often have fields that are now inherited automatically. Remove them — they'll cause conflicts if left in:

### `IsCalculatedField` and `CalculateField`

These are now part of `CalculationModel`. Remove them from your model:

```python
# Before
from generic_app.generic_models.upload_model import IsCalculatedField, CalculateField

class CalculateNAV(ConditionalUpdateMixin):
    is_calculated = IsCalculatedField(default=False)
    calculate = CalculateField()
    # ... your fields ...
```

```python
# After
from lex.core.models.CalculationModel import CalculationModel

class CalculateNAV(CalculationModel):
    # is_calculated and calculate are inherited — don't define them
    # ... your fields ...
```

> [!warning]
> If you leave `IsCalculatedField` or `CalculateField` in your model, Django will raise a conflict error. These fields are now provided by `CalculationModel` automatically.

### `dont_update` Flag

V1 used a `dont_update` boolean to prevent recursion in calculations. This is handled automatically now — remove it:

```python
# Before
class CalculateNAV(ConditionalUpdateMixin):
    dont_update = False

    def update(self):
        self.dont_update = True
        # ... logic ...
        self.save()
        self.dont_update = False
```

```python
# After
class CalculateNAV(CalculationModel):
    # No recursion guard needed — the framework handles this

    def calculate(self):
        # ... logic ...
        # No self.save() needed either
```

## Update `__str__` Methods

V1 models sometimes used `generic_app` utilities in their `__str__` methods. Make sure they use standard Python:

```python
# This should already work — just verify
def __str__(self):
    return f"{self.name} — {self.vintage_year}"
```

## Update ForeignKey References

If your V1 models used string references to other models, verify they still resolve correctly:

```python
# String reference (always works)
team = models.ForeignKey('Team', on_delete=models.CASCADE)

# Direct import — input model
from Input.Team import Team
team = models.ForeignKey(Team, on_delete=models.CASCADE)

# Direct import — model in a subfolder
from Upload.FundUpload import FundUpload
```

> [!tip]
> String references like `'Team'` are usually the safest option during migration — they avoid circular import issues.

## Verify

At this point, your models should:

1. Inherit from `LexModel` or `CalculationModel` (not `Model`, `UploadModelMixin`, or `ConditionalUpdateMixin`)
2. Not define `is_calculated`, `calculate`, or `dont_update` fields
3. Have clean `__str__` methods
4. Have working ForeignKey references

```bash
# Should return zero results
grep -rn "ConditionalUpdateMixin\|UploadModelMixin\|IsCalculatedField\|CalculateField\|dont_update" --include="*.py" .
```

## Checkpoint

- [ ] All standard models inherit from `LexModel`
- [ ] Calculation models inherit from `CalculationModel` (conversion in [[migration/refactoring/Part 3 — Calculations|Part 3]])
- [ ] `IsCalculatedField`, `CalculateField`, and `dont_update` removed
- [ ] ForeignKey references work with flat layout
- [ ] Zero `grep` results for legacy base classes and fields

Next: [[migration/refactoring/Part 3 — Calculations|Part 3 — Calculations]].
