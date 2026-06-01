---
title: "CalculatedModelMixin Internals"
---

`CalculatedModelMixin` extends [[reference/LexModel Internals|LexModel]] with a **combination engine** — it generates, deduplicates, clusters, and dispatches model instances from a cartesian product of defining fields. It's the base class for any model that needs to produce many records from dimensional combinations.

> [!tip]
> Browse the full source on [GitHub](https://github.com/ExcellenceCloudGmbH/lex-app/blob/lex-app-v2/lex/core/mixins/CalculatedModelMixin.py).

```python
from lex.core.mixins.CalculatedModelMixin import CalculatedModelMixin
```

For the conceptual guide with examples and patterns, see [[features/processing/batch calculations]].

## Class Attributes

| Attribute | Type | Default | Purpose |
|---|---|---|---|
| `defining_fields` | `List[str]` | `[]` | Field names that form the combination axes. A `UniqueConstraint` is created automatically. |
| `parallelizable_fields` | `List[str]` | `[]` | Subset of `defining_fields` used to group models for Celery dispatch. |
| `input` | `bool` | `False` | Whether this model accepts external input data. |

## Required Methods

### `get_selected_key_list(key: str) → list`

Returns the possible values for a single defining field. Called once per field during combination generation.

```python
def get_selected_key_list(self, key: str) -> list:
    if key == 'region':
        return list(Region.objects.all())
    if key == 'product':
        return list(Product.objects.filter(active=True))
    return []
```

**Important:** By the time a field is expanded, all fields listed *before* it in `defining_fields` are already set on `self`. This lets you filter dependent fields (e.g., awards belonging to the current upload).

### `calculate()`

Business logic for one combination. Same contract as `CalculationModel.calculate()` — don't call `self.save()`, the framework handles it (without bumping `edited_by` / `edited_at` during calculation-triggered saves).

## The `create()` Classmethod

```python
MyBatchModel.create(**kwargs)
```

Orchestrates the four-step pipeline:

### Step 1 — `ModelCombinationGenerator`

Expands `defining_fields` into the cartesian product. For each field:
1. Check if `kwargs` contains an override → use it
2. Otherwise call `get_selected_key_list(field_name)` on the current model instance
3. Deep-copy the model for each value and set the field

Fields with overrides are processed **first** to reduce the search space early.

### Step 2 — Duplicate Handling

For each generated instance, queries the database for existing records matching all `defining_fields`:

| Matches | Action |
|---|---|
| 0 | Insert — primary key is reset for a fresh record |
| 1 | Update — reuses the existing record's primary key |
| > 1 | Error — data integrity violation |

This makes `create()` **idempotent**.

### Step 3 — `ModelClusterManager`

Groups models into processing batches based on `parallelizable_fields`:

```python
# parallelizable_fields = ['region']
# Produces:
{'US': [m1, m2, m3], 'EU': [m4, m5], 'APAC': [m6, m7, m8]}
# Flattened to: [[m1,m2,m3], [m4,m5], [m6,m7,m8]]
```

If `parallelizable_fields` is empty, all models go into a single group.

### Step 4 — Dispatch

Checks `CELERY_ACTIVE` environment variable and whether `calculate()` has a `.delay()` attribute:

- **Celery path:** `CeleryTaskDispatcher.dispatch_calculation_groups()` sends each group as a `calc_and_save.delay()` call inside a `WaitForTasks` context, then waits for completion.
- **Sync path:** `calc_and_save_sync()` processes all models sequentially — calls `lex_func()` then `save()` on each. `None` models are skipped. Individual failures are logged and collected; an error is only raised if every model in the batch fails.

In both paths, the framework sets logging context for the currently executing child model automatically.

Failed Celery groups are automatically retried synchronously.

## The `CalculatedModelMixinMeta` Metaclass

The custom metaclass does one important thing: it reads `defining_fields` and creates a [`UniqueConstraint`](https://docs.djangoproject.com/en/stable/ref/models/constraints/#uniqueconstraint) on those fields automatically. This enforces at the database level that no two records can have the same combination of defining field values.

```python
# Automatically generated:
class Meta:
    constraints = [
        UniqueConstraint(fields=['upload', 'award'], name='defining_fields_MyModel')
    ]
```

## Method Resolution: `lex_func()`

`CalculatedModelMixin` supports both `calculate()` and `calculate_mixin()` as override targets. The `lex_func()` method resolves which one to call:

1. If `calculate_mixin()` is overridden → use it
2. Otherwise if `calculate()` is overridden → use it
3. Otherwise → `NotImplementedError`

In practice, always override `calculate()`. The `calculate_mixin()` path exists for internal framework use.

## Inherited Features

Since `CalculatedModelMixin` extends `LexModel`, your batch models also get:

- `created_by` / `edited_by` tracking
- `pre_validation()` / `post_validation()` hooks
- All `permission_*()` methods
- Full [[features/tracking/bitemporal history|bitemporal history]] (unless listed in `untracked_models`)

## Error Hierarchy

The framework provides specific exception types for each stage:

| Exception | When |
|---|---|
| `ModelCombinationError` | Field expansion fails — bad field name, empty values, `get_selected_key_list()` error |
| `ModelClusteringError` | Clustering fails — invalid parallelizable field, grouping error |
| `CeleryDispatchError` | Celery dispatch fails (triggers automatic sync fallback) |
| `CalculatedModelError` | General error during the pipeline |

## Quick Reference

```python
from lex.core.mixins.CalculatedModelMixin import CalculatedModelMixin

class MyBatchModel(CalculatedModelMixin):
    # Dimensions
    defining_fields = ['upload', 'item']
    parallelizable_fields = ['upload']

    # Fields
    upload = models.ForeignKey('Upload', on_delete=models.CASCADE)
    item = models.ForeignKey('Item', on_delete=models.CASCADE)
    result = models.FloatField()

    # Values for each dimension
    def get_selected_key_list(self, key: str) -> list:
        if key == 'item':
            return list(Item.objects.filter(upload=self.upload))

    # Business logic per combination
    def calculate(self):
        self.result = compute(self.upload, self.item)

# Generate all combinations
MyBatchModel.create()

# Generate with overrides
MyBatchModel.create(upload=[specific_upload])
```
