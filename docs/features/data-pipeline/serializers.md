---
title: Serializers
---

Lex App automatically generates a REST API for every model. By default, all fields are exposed as-is. When you need custom validation, computed fields, or different views of the same model, you create a `serializers.py` file using [Django REST Framework](https://www.django-rest-framework.org/api-guide/serializers/).

## How It Works

1. Create a `serializers.py` file in the same folder as your models
2. Define one or more serializer classes
3. Attach them to your model via `Model.api_serializers`

The framework picks them up automatically — no registration or configuration needed.

## Basic Example

Here's a serializer for an `Expense` model that enforces business rules — positive amounts, a cap on meal expenses, and correct handling of partial updates (PATCH requests from inline grid editing):

```python title="Input/serializers.py"
from rest_framework import serializers
from lex.api.views.model_entries.mixins.PermissionAwareSerializerMixin import add_permission_checks

from Input.Expense import Expense


@add_permission_checks
class ExpenseDefaultSerializer(serializers.ModelSerializer):
    class Meta:
        model = Expense
        fields = '__all__'

    def validate_amount(self, value):
        """Amounts must be positive."""
        if value <= 0:
            raise serializers.ValidationError("Amount must be positive.")
        return value

    def validate(self, attrs):
        """Enforce business rules across fields.

        On partial updates (PATCH) attrs only contains the fields that
        were sent in the request, so we fall back to the existing
        instance for any field the user did not touch.
        """
        amount = attrs.get('amount')
        category = attrs.get('category')

        if self.instance:
            if amount is None:
                amount = self.instance.amount
            if category is None:
                category = self.instance.category

        if amount and amount > 5000 and category == 'meals':
            raise serializers.ValidationError(
                {'amount': "Meal expenses over €5,000 are not allowed."}
            )
        return attrs


Expense.api_serializers = {
    'default': ExpenseDefaultSerializer,
}
```

This example shows three important patterns:

1. **`@add_permission_checks`** — integrates with the [[features/access-and-ui/permissions|permission system]], enforcing `permission_read()` and `permission_edit()` at the API level
2. **`validate_<field>`** — field-level validation that runs automatically and shows errors inline in the grid
3. **`validate()` with `self.instance` fallback** — cross-field validation that works correctly for both full saves (POST/PUT) and inline cell edits (PATCH, which only sends the changed field)

> [!warning] PATCH and cross-field validation
> When a user edits a single cell in the grid, the frontend sends a **PATCH** request containing only that field. In `validate()`, `attrs` won't include the fields the user didn't touch. Always fall back to `self.instance` for the "other" field — otherwise the rule silently passes.

## More Validation Patterns

Here are additional patterns you can mix and match in your serializers:

### Date and temporal checks

```python
from django.utils import timezone

def validate_report_date(self, value):
    """Report date must not be in the future."""
    if value > timezone.now():
        raise serializers.ValidationError("Report date cannot be in the future.")
    return value
```

### Conditional required fields

```python
def validate(self, attrs):
    """Locked quarters must have a report date."""
    locked = attrs.get('locked', getattr(self.instance, 'locked', False))
    report_date = attrs.get('report_date', getattr(self.instance, 'report_date', None))

    if locked and report_date is None:
        raise serializers.ValidationError({
            'report_date': "Locked quarters must have a report date."
        })
    return attrs
```

### Computed read-only fields

```python
class InvestorCashflowDetailSerializer(serializers.ModelSerializer):
    amount_display = serializers.SerializerMethodField()

    class Meta:
        model = InvestorCashflow
        fields = ['id', 'investor', 'amount_eur', 'amount_display']

    def get_amount_display(self, obj):
        return f"€{obj.amount_eur:,.2f}" if obj.amount_eur else None
```

When validation fails, the error message appears directly in the frontend UI — both for field-level (`validate_<field>`) and object-level (`validate()`) errors.

> [!note]
> Computed serializer-only fields work well for display, exports, and detail views. But if a field only exists in the serializer — for example via `SerializerMethodField()` — you can't use it as a row-group or pivot column in the grid. If you need grouping or pivoting, use a real model field.

> [!tip] Serializer validation vs. `pre_validation()`
> Serializer validation and [[features/data-pipeline/lifecycle hooks#`pre_validation()` — Guard Before Save|pre_validation()]] both block invalid data before it's saved — but they run at different layers. Serializer validation runs in the **API layer** (when data arrives via REST), while `pre_validation()` runs in the **model layer** (on every `save()`, regardless of source). If a rule should apply no matter how the model is saved — API, management command, hook, calculation — put it in `pre_validation()`. If it's specific to the REST API (e.g., formatting, permission-aware checks), use a serializer.

## Multiple Serializer Views

You can define multiple serializers for the same model — for example, a default view and a more detailed view:

```python title="Cashflows/serializers.py"
from rest_framework import serializers

from Cashflows.InvestorCashflow import InvestorCashflow


class InvestorCashflowDefaultSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvestorCashflow
        fields = '__all__'


class InvestorCashflowDetailSerializer(serializers.ModelSerializer):
    extra_info = serializers.CharField(read_only=True)

    class Meta:
        model = InvestorCashflow
        fields = ['id', 'investor', 'vehicle', 'amount_eur',
                  'transaction_date', 'extra_info']


InvestorCashflow.api_serializers = {
    'default': InvestorCashflowDefaultSerializer,
    'detail': InvestorCashflowDetailSerializer,
}
```

| Key         | When It's Used                               |
| ----------- | -------------------------------------------- |
| `'default'` | List view and standard API calls             |
| `'detail'`  | Detail view when a specific record is opened |

> [!note] The `id` field is always present
> When you override `api_serializers["default"]`, the framework always includes the model's primary key as `id` in the serialized output — even if your `Meta.fields` omits it. Row navigation, edit URLs, and the CRUD loading overlay all depend on this field.

### Renaming the framework serializer

When you override `api_serializers["default"]` with a custom serializer, the framework's auto-generated serializer is replaced in the map — your serializer becomes `"default"`. For most models this is exactly what you want.

If you also need the framework-generated serializer to remain accessible (for example, so users can switch back to it in the View Preset dropdown, or if internal features like history snapshots rely on it), set `DEFAULT_SERIALIZER_NAME` in `lex_config.py`:

```python title="lex_config.py"
DEFAULT_SERIALIZER_NAME = "framework_default"
```

With this setting, the framework-generated serializer is additionally registered under `"framework_default"`. Both keys appear in the **View Preset** dropdown. Without this setting, the framework serializer is only available while no `"default"` override exists — once you add one, the framework serializer is no longer in the map.

### Computed fields and row grouping

`SerializerMethodField` fields — and any other computed property with no underlying database column — are automatically marked as non-groupable. The grid will disable **row group** and **pivot** for those columns. Only real database-backed columns support server-side grouping.

> [!tip] History tables pick up your default override automatically
> When you override `api_serializers["default"]`, the framework also registers the auto-generated serializer under the same alias on the model's history and meta-history tables. You don't need to configure anything on `HistoricalInvestorCashflow` or `MetaHistoricalInvestorCashflow`—history-endpoint and snapshot lookups stay consistent for free.

## Where to Put Serializers

We recommend **one `serializers.py` file per folder**, containing all serializers for the models in that folder:

```
Input/
├── __init__.py
├── Team.py
├── Employee.py
├── Expense.py
└── serializers.py         ← serializers for all Input models
Reports/
├── __init__.py
├── BudgetSummary.py
└── serializers.py         ← serializers for all Report models
```

You _could_ create a separate file per serializer (e.g., `ExpenseSerializer.py`), but in practice a single file per folder is easier to navigate — you can see all validation rules and field configurations for related models in one place, and the imports stay clean since the models are all in the same folder.

> [!note]
> You don't _have_ to create serializers. Without them, Lex App generates a default serializer that exposes all fields. Custom serializers are for when you need validation, computed fields, or restricted views.

For more on the REST API and [Django REST Framework](https://www.django-rest-framework.org/), see the official DRF documentation.
