# Serializers & API Layer

Search keywords: LexSerializer, dynamic serializer, add_permission_checks, CRUD endpoints, validate, PATCH, api_serializers

## Scope

- Serializer generation and customization
- Permission-aware serialization via `@add_permission_checks`
- Validation patterns (field-level, cross-field, PATCH-safe)
- Multiple serializer views
- File organization

## Key Points

- Lex auto-generates a REST API for every model. Default serializers expose all fields.
- Custom serializers use Django REST Framework and are attached via `Model.api_serializers`.
- `@add_permission_checks` integrates with the permission system at the API level.
- PATCH requests (inline grid edits) only send the changed field — cross-field validation must fall back to `self.instance`.

## Import

```python
from rest_framework import serializers
from lex.api.views.model_entries.mixins.PermissionAwareSerializerMixin import add_permission_checks
```

## Basic Example

```python
# Input/serializers.py
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
        """Cross-field validation — PATCH-safe."""
        amount = attrs.get('amount')
        category = attrs.get('category')

        # Fall back to instance for fields not in PATCH request
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

### Key patterns:

1. **`@add_permission_checks`** — enforces `permission_read()`/`permission_edit()` at API level
2. **`validate_<field>`** — field-level validation, errors shown inline in grid
3. **`validate()` with `self.instance` fallback** — cross-field validation safe for PATCH

> **PATCH warning:** When a user edits a single cell, the frontend sends a PATCH with only that field. In `validate()`, `attrs` won't include untouched fields. Always fall back to `self.instance`.

## More Validation Patterns

### Date checks

```python
def validate_report_date(self, value):
    if value > timezone.now():
        raise serializers.ValidationError("Report date cannot be in the future.")
    return value
```

### Conditional required fields

```python
def validate(self, attrs):
    locked = attrs.get('locked', getattr(self.instance, 'locked', False))
    report_date = attrs.get('report_date', getattr(self.instance, 'report_date', None))
    if locked and report_date is None:
        raise serializers.ValidationError({
            'report_date': "Locked quarters must have a report date."
        })
    return attrs
```

## Multiple Serializer Views

```python
MyModel.api_serializers = {
    'default': MyModelDefaultSerializer,    # list view, standard API
    'detail': MyModelDetailSerializer,      # detail view
}
```

## Hide Default Table Actions Column

If a serializer-backed table should not show Lex's default row actions column,
set `hide_actions_column = True` on the serializer `Meta`.

```python
from rest_framework import serializers
from lex.api.views.model_entries.mixins.PermissionAwareSerializerMixin import add_permission_checks
from Input.Expense import Expense

@add_permission_checks
class ExpenseTableSerializer(serializers.ModelSerializer):
    class Meta:
        model = Expense
        fields = ('id', 'name', 'amount', 'status')
        hide_actions_column = True


Expense.api_serializers = {
    'default': ExpenseTableSerializer,
}
```

Lex suppresses the default Show/Edit/Delete column for tables that use this
serializer. Leave the option unset to keep the current behavior.

## File Organization

One `serializers.py` per folder:

```
Input/
├── __init__.py
├── Team.py
├── Employee.py
├── Expense.py
└── serializers.py         ← all Input model serializers
```

## Serializer vs. pre_validation()

| Context                          | Use                         |
| -------------------------------- | --------------------------- |
| Universal rule (every save)      | `pre_validation()` on model |
| API-specific (formatting, PATCH) | Serializer `validate()`     |

## Where to Expand

- `lex_context.md`: Serializers & API Integration
- `lex_context_repo.md`: Serializers — API Data Layer; API Endpoints Reference

## LLM Prompt Starters

- "Generate a custom serializer with `@add_permission_checks` and PATCH-safe cross-field validation."
- "Map this model to expected CRUD endpoints with serializer validation rules."
