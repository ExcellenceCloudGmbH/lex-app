# CalculatedModelMixin Combinatorics

Search keywords: CalculatedModelMixin, defining_fields, combinations, create workflow, duplicate handling, get_selected_key_list, parallelizable_fields

## Scope

- Batch combinatorial model generation
- Defining field semantics and combination engine
- Duplicate handling and idempotent `create()`
- Parallel dispatch with `parallelizable_fields`

## Import

```python
from lex.core.mixins.CalculatedModelMixin import CalculatedModelMixin
```

## Key Concepts

`CalculatedModelMixin` generates model instances from the cartesian product of `defining_fields`. You declare dimensions + calculation logic, and the framework handles expansion, deduplication, clustering, and dispatch.

### Class Attributes

| Attribute | Type | Default | Purpose |
|---|---|---|---|
| `defining_fields` | `List[str]` | `[]` | Field names forming the combination axes. Auto-creates `UniqueConstraint`. |
| `parallelizable_fields` | `List[str]` | `[]` | Subset of `defining_fields` for grouping Celery tasks. |

## Required Methods

### `get_selected_key_list(key: str) → list`

Returns possible values for one defining field:

```python
def get_selected_key_list(self, key: str) -> list:
    if key == 'award':
        return list(Award.objects.filter(upload=self.upload))
    return []
```

**Important:** Fields are expanded in `defining_fields` order. By the time a field is expanded, all earlier fields are already set on `self`.

### `calculate()`

Business logic for one combination. Same contract as `CalculationModel.calculate()` — don't call `self.save()`.

## Complete Example

```python
from lex.core.mixins.CalculatedModelMixin import CalculatedModelMixin
from django.db import models

class AssetCalculation(CalculatedModelMixin):
    upload = models.ForeignKey('Upload', on_delete=models.CASCADE)
    award = models.ForeignKey('Award', on_delete=models.CASCADE)

    award_amount = models.FloatField()
    market_value = models.FloatField()
    gain_loss = models.FloatField()

    defining_fields = ['upload', 'award']

    def get_selected_key_list(self, key: str) -> list:
        if key == 'award':
            return list(Award.objects.filter(upload=self.upload))

    def calculate(self):
        self.award_amount = self.award.original_value
        self.market_value = self.award.current_value
        self.gain_loss = self.market_value - self.award_amount

# Trigger: generate all combinations and calculate
AssetCalculation.create()

# With overrides
AssetCalculation.create(upload=[specific_upload])
```

## The Combination Engine

Combination is **multiplicative**. With:
- `defining_fields = ['region', 'product', 'scenario']`
- `region` → 3 values, `product` → 2 values, `scenario` → 2 values

Result: **3 × 2 × 2 = 12** instances.

## The Four-Step Pipeline

When `MyModel.create()` is called:

### Step 1 — Generate Combinations
`ModelCombinationGenerator` expands each field via `get_selected_key_list()` (or `kwargs` overrides). Deep-copies the base model for each value.

### Step 2 — Handle Duplicates
Queries database for existing records with same defining field values:

| Existing | Action |
|---|---|
| 0 | Insert — new record |
| 1 | Update — reuses existing PK |
| > 1 | Error — data integrity violation |

Makes `create()` **idempotent**.

### Step 3 — Cluster into Groups
`ModelClusterManager` groups by `parallelizable_fields`. Empty = single group.

### Step 4 — Dispatch
Checks `CELERY_ACTIVE` + `@lex_shared_task`. If both true → Celery dispatch per group. Otherwise → synchronous. Failed Celery groups retried synchronously.

## Parallel Processing

```python
class LiabilityCalculation(CalculatedModelMixin):
    upload = models.ForeignKey('Upload', on_delete=models.CASCADE)
    award = models.ForeignKey('Award', on_delete=models.CASCADE)

    defining_fields = ['upload', 'award']
    parallelizable_fields = ['upload']
```

| `parallelizable_fields` | Grouping | Result |
|---|---|---|
| `[]` (empty) | All in one group | 1 task |
| `['upload']` | One per upload | 3 uploads → 3 tasks |
| `['upload', 'region']` | One per (upload, region) pair | 3 × 4 → 12 tasks |

## Common Patterns

### Pattern 1: Upload → Per-Record Calculations
```python
defining_fields = ['upload', 'award']
def get_selected_key_list(self, key):
    if key == 'award':
        return list(Award.objects.filter(upload=self.upload))
```

### Pattern 2: Report Per Upload
```python
defining_fields = ['upload']
def get_selected_key_list(self, key):
    if key == 'upload':
        return list(Upload.objects.all())
```

### Pattern 3: Multi-Dimensional Grid
```python
defining_fields = ['region', 'product', 'scenario']
parallelizable_fields = ['region']
```

## CalculatedModelMixin vs CalculationModel

| | `CalculationModel` | `CalculatedModelMixin` |
|---|---|---|
| **Purpose** | Single-record calculation | Batch generation of many records |
| **Trigger** | User clicks **Calculate ▶️** | `cls.create()` called programmatically |
| **Records** | One existing record | Creates/updates many records |
| **State machine** | Yes | No |
| **Use case** | "Calculate this report" | "Generate a liability for every award" |

They work together: a `CalculationModel` trigger's `calculate()` can call `CalculatedModelMixin.create()`.

## Where to Expand

- `lex_context.md`: CalculatedModelMixin — Combinatorial Batch Processing
- `lex_context_repo.md`: CalculatedModelMixin — Combinatorial Model Processing

## LLM Prompt Starters

- "Implement a CalculatedModelMixin example with defining fields and safe duplicate handling."
- "Given selected key lists, explain the expansion workflow and resulting instance count."
