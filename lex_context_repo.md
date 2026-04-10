# Lex Framework — AI Assistant Context Document

> **Purpose**: This document is the complete reference for an AI coding assistant helping developers build Python projects on top of the Lex framework. It covers every subsystem, class, method, pattern, and convention. Use this document as your primary source of truth when generating code, answering questions, or debugging Lex-based projects.

---

## TABLE OF CONTENTS

1. [What Is Lex](#1-what-is-lex)
2. [Architecture Overview](#2-architecture-overview)
3. [Project Structure for Lex Users](#3-project-structure-for-lex-users)
4. [Models — LexModel Base Class](#4-models--lexmodel-base-class)
5. [CalculationModel — Status-Tracked Calculations](#5-calculationmodel--status-tracked-calculations)
6. [CalculatedModelMixin — Combinatorial Model Processing](#6-calculatedmodelmixin--combinatorial-model-processing)
7. [Permissions & Authorization](#7-permissions--authorization)
8. [Modification Restrictions](#8-modification-restrictions)
9. [Serializers — API Data Layer](#9-serializers--api-data-layer)
10. [Custom Fields (Bokeh, HTML, PDF, XLSX)](#10-custom-fields-bokeh-html-pdf-xlsx)
11. [Filters](#11-filters)
12. [Process Admin — Model Registration & Structure](#12-process-admin--model-registration--structure)
13. [Audit Logging](#13-audit-logging)
14. [LexLogger — Fluent Markdown Logging](#14-lexlogger--fluent-markdown-logging)
15. [Celery Integration — Async Task Dispatch](#15-celery-integration--async-task-dispatch)
16. [Transactions & Deferred Recalculation](#16-transactions--deferred-recalculation)
17. [Signals & Dependency Cascade](#17-signals--dependency-cascade)
18. [WebSocket Consumers — Real-Time Updates](#18-websocket-consumers--real-time-updates)
19. [Authentication & Keycloak](#19-authentication--keycloak)
20. [Streamlit Integration](#20-streamlit-integration)
21. [HTMLReport — Custom HTML Views](#21-htmlreport--custom-html-views)
22. [Process — Abstract Workflow Definition](#22-process--abstract-workflow-definition)
23. [CLI Commands](#23-cli-commands)
24. [Settings & Environment Variables](#24-settings--environment-variables)
25. [Import System — Module Aliasing](#25-import-system--module-aliasing)
26. [Utilities & Decorators](#26-utilities--decorators)
27. [Common Patterns & Conventions](#27-common-patterns--conventions)
28. [Quick Reference — What to Import From Where](#28-quick-reference--what-to-import-from-where)
29. [Complete Code Examples](#29-complete-code-examples)

---

## 1. What Is Lex

Lex is a full-stack Django-based application framework for building data-driven business applications. It provides:

- **Auto-discovered Django models** with built-in CRUD REST APIs
- **Permission system** with Keycloak UMA integration and field-level access control
- **Calculation engine** with Celery-based parallel processing and combinatorial model expansion
- **Audit logging** with WebSocket real-time notifications
- **Streamlit** integration for data visualization
- **React Admin** frontend (auto-generated from model structure)
- **Model structure YAML** for hierarchical UI navigation

**Tech stack**: Django, Django REST Framework, Django Channels, Celery, Redis, PostgreSQL, Keycloak, Streamlit, React Admin.

---

## 2. Architecture Overview

```
User Project (your code)
    │
    ├── models.py          ← Your Django models (extend LexModel)
    ├── _structure.py       ← Model structure definition (optional)
    ├── _styling.py         ← Model styling (optional)
    ├── _streamlit_structure.py ← Streamlit pages (optional)
    ├── _authentication_settings.py ← Auth config (optional)
    └── ...
    │
    ▼
Lex Framework (this repo)
    │
    ├── lex/core/           ← Base models, mixins, calculations, signals
    ├── lex/api/            ← REST API views, serializers, filters, WebSocket consumers
    ├── lex/process_admin/  ← Model registry, structure, URL routing
    ├── lex/audit_logging/  ← Audit trail, LexLogger, calculation logs
    ├── lex/authentication/ ← Keycloak, OIDC, JWT, user profiles
    ├── lex/utilities/      ← App config, singletons, import system, storage
    ├── lex/lex_app/        ← Django project config, Celery, settings, management commands
    └── lex/bin/            ← CLI entry point
```

### Request Flow

```
HTTP Request
  → Django Middleware (KeycloakPermissionsMiddleware)
  → DRF View (OneModelEntry / ListModelEntries / ManyModelEntries)
  → Permission Check (UserPermission + modification_restriction)
  → Serializer (LexSerializer with field-level permission filtering)
  → Model (LexModel with validation hooks)
  → Response (with lex_reserved_scopes per record)
```

### Calculation Flow

```
Model.is_calculated = "In Progress"
  → AFTER_CREATE / AFTER_UPDATE hook fires
  → Celery available? → dispatch_calculation_task() → Worker
  → Celery unavailable? → execute_calculation_sync()
  → Status → SUCCESS / ERROR
  → WebSocket notification → Frontend updates
```

---

## 3. Project Structure for Lex Users

A Lex user project is a Python package that sits alongside the Lex framework. Lex auto-discovers your models via filesystem walk.

### Minimal Project

```
my_project/
├── __init__.py
├── requirements.txt
├── .env                         # Keycloak credentials
├── models.py                    # Your models (or subdirectories)
└── model_structure.yaml         # UI navigation structure (optional)
```

### Full Project

```
my_project/
├── __init__.py
├── requirements.txt
├── .env
├── module_a/
│   ├── __init__.py
│   ├── models.py                # Models auto-discovered
│   └── sub_module/
│       └── more_models.py       # Also auto-discovered
├── module_b/
│   └── calculations.py          # CalculatedModelMixin subclasses
├── _structure.py                # get_model_structure(), get_model_styling()
├── _streamlit_structure.py      # main() for Streamlit
├── _authentication_settings.py  # Auth settings (initial_data_load, etc.)
└── migrations/
    └── __init__.py
```

### File Naming Conventions

| File | Purpose |
|------|---------|
| Any `.py` with `models.Model` subclasses | Auto-discovered and registered |
| `_structure.py` or `model_structure.yaml` | Defines UI navigation tree |
| `_styling.py` | Model display customization |
| `_streamlit_structure.py` | Streamlit app entry point |
| `_authentication_settings.py` | Auth config (`initial_data_load`, etc.) |
| Files starting with `_` or `.` | Excluded from model discovery |
| Files ending with `_test` | Excluded from model discovery |

### Excluded from Auto-Discovery

Files/dirs named: `asgi`, `wsgi`, `settings`, `urls`, `setup`, `venv`, `.venv`, `build`, `migrations`, and anything starting with `_` or `.`.

---

## 4. Models — LexModel Base Class

`LexModel` is the abstract base class for ALL domain models in Lex. It extends `django-lifecycle`'s `LifecycleModel`.

### Import

```python
from lex.core.models import LexModel
```

### Built-in Fields

| Field | Type | Description |
|-------|------|-------------|
| `created_by` | `TextField(null=True, blank=True, editable=False)` | Auto-set on creation from request user |
| `edited_by` | `TextField(null=True, blank=True, editable=False)` | Auto-set on update from request user |

### Basic Model Example

```python
from django.db import models
from lex.core.models import LexModel

class Product(LexModel):
    name = models.CharField(max_length=200)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    category = models.ForeignKey('Category', on_delete=models.CASCADE, null=True)
    description = models.TextField(blank=True)

    class Meta:
        verbose_name = "Product"
        verbose_name_plural = "Products"

    def __str__(self):
        return self.name
```

### Validation Hooks

LexModel provides a validation-with-rollback mechanism via `django-lifecycle` hooks:

```python
class Product(LexModel):
    name = models.CharField(max_length=200)
    price = models.DecimalField(max_digits=10, decimal_places=2)

    def pre_validation(self):
        """Called BEFORE save. Raise an exception to cancel the save entirely."""
        if self.price < 0:
            raise ValueError("Price cannot be negative")

    def post_validation(self):
        """Called AFTER save. Raise an exception to trigger a rollback to the pre-save state."""
        if self.name == "FORBIDDEN":
            raise ValueError("This product name is not allowed")
```

**How rollback works**:
1. `pre_validation_hook` (BEFORE_SAVE) captures a snapshot of all fields
2. `pre_validation()` runs — raise to cancel save
3. Django saves the model
4. `post_validation_hook` (AFTER_SAVE) runs `post_validation()`
5. If `post_validation()` raises → `_execute_rollback()` restores the snapshot via savepoint

### History Tracking

Models are auto-registered with `django-simple-history` unless excluded. Control tracking:

```python
instance.untrack()  # Disable history for next save
instance.save()
instance.track()    # Re-enable history
```

### Streamlit Methods

Override these to add Streamlit visualizations:

```python
class Product(LexModel):
    # ...

    def streamlit_main(self, user=None):
        """Instance-level Streamlit visualization. Called with ?model=product&pk=123"""
        import streamlit as st
        st.write(f"Product: {self.name}")
        st.metric("Price", f"${self.price}")

    @classmethod
    def streamlit_class_main(cls):
        """Class-level Streamlit visualization. Called with ?model=product"""
        import streamlit as st
        st.write(f"All {cls.__name__} records")
        st.dataframe(cls.objects.values())
```

---

## 5. CalculationModel — Status-Tracked Calculations

For models that undergo calculations with status tracking (progress indicators, success/error states).

### Import

```python
from lex.core.models import CalculationModel
```

### Status Constants

| Constant | Value | Meaning |
|----------|-------|---------|
| `CalculationModel.IN_PROGRESS` | `"In Progress"` | Calculation is running |
| `CalculationModel.SUCCESS` | `"Success"` | Calculation completed successfully |
| `CalculationModel.ERROR` | `"Error"` | Calculation failed |
| `CalculationModel.NOT_CALCULATED` | `"Not Calculated"` | Initial state |
| `CalculationModel.ABORTED` | `"Aborted"` | Calculation was aborted (e.g., server restart) |

### Built-in Field

| Field | Type | Description |
|-------|------|-------------|
| `is_calculated` | `CharField(max_length=50, choices=STATUSES, default=NOT_CALCULATED, editable=False)` | Current calculation status |

### Usage

```python
from django.db import models
from lex.core.models import CalculationModel

class Report(CalculationModel):
    name = models.CharField(max_length=200)
    input_data = models.JSONField(default=dict)
    result = models.JSONField(default=dict, blank=True)

    def calculate(self):
        """Override this. Called when is_calculated becomes IN_PROGRESS."""
        # Your calculation logic here
        self.result = {"total": sum(self.input_data.values())}
        self.save()

    def update(self):
        """Alternative: override this for update-triggered calculations."""
        pass

    class Meta:
        verbose_name = "Report"
```

### How It Works

1. Frontend or code sets `is_calculated = "In Progress"`
2. `calculate_hook` fires (AFTER_UPDATE or AFTER_CREATE when `is_calculated == IN_PROGRESS`)
3. `lex_func()` selects which method to run (`calculate` or `update` — whichever you overrode)
4. If `CELERY_ACTIVE=true` → dispatches to Celery worker
5. If Celery unavailable → runs synchronously
6. On completion: `is_calculated` → `SUCCESS` or `ERROR`
7. WebSocket notification sent to frontend

### Important Notes

- The `calculate_hook` auto-detects whether you overrode `calculate()` or `update()` and calls the right one
- Always `self.save()` your results inside `calculate()` — the framework only manages status
- Celery dispatch requires `CELERY_ACTIVE=true` env var and a running Redis broker

---

## 6. CalculatedModelMixin — Combinatorial Model Processing

The most powerful feature: generates all combinations of defining fields and processes them in parallel.

### Import

```python
from lex.core.mixins import CalculatedModelMixin
```

### Class Attributes

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `defining_fields` | `List[str]` | `[]` | Fields that create unique combinations. A `UniqueConstraint` is auto-created. |
| `parallelizable_fields` | `List[str]` | `[]` | Subset of `defining_fields` used for Celery parallelization grouping |
| `input` | `bool` | `False` | Whether model accepts input data |

### Usage

```python
from django.db import models
from lex.core.mixins import CalculatedModelMixin

class SalesProjection(CalculatedModelMixin):
    region = models.CharField(max_length=100)
    product = models.CharField(max_length=100)
    year = models.IntegerField()
    projected_sales = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    defining_fields = ['region', 'product', 'year']
    parallelizable_fields = ['region']  # Group by region for parallel processing

    def get_selected_key_list(self, key):
        """Return all possible values for a defining field."""
        if key == 'region':
            return ['US', 'EU', 'APAC']
        elif key == 'product':
            return ['Widget A', 'Widget B']
        elif key == 'year':
            return [2024, 2025, 2026]
        return []

    def calculate(self):
        """Called for each combination. self.region, self.product, self.year are set."""
        # Your calculation logic here
        self.projected_sales = compute_projection(self.region, self.product, self.year)

    class Meta:
        verbose_name = "Sales Projection"
```

### Triggering Calculation

```python
# Create a base instance and trigger combinatorial expansion
SalesProjection.create(region='US', product='Widget A', year=2024)
```

### The `create()` 4-Step Workflow

1. **Generate combinations**: `ModelCombinationGenerator` expands `defining_fields` using `get_selected_key_list()`. E.g., 3 regions × 2 products × 3 years = 18 model instances.
2. **Prepare models**: Handles duplicates via `delete_models_with_same_defining_fields()`. If an existing record matches the defining fields, it's reused.
3. **Create clusters**: `ModelClusterManager` groups by `parallelizable_fields` into nested dicts. E.g., `{'US': [6 models], 'EU': [6 models], 'APAC': [6 models]}`.
4. **Dispatch**: If `CELERY_ACTIVE=true` → each cluster dispatched as a Celery task group. Otherwise → `calc_and_save_sync()` processes sequentially.

### Field Overrides

Pass keyword arguments to `create()` to override specific field values instead of expanding:

```python
# Only for region='US', expand product and year normally
SalesProjection.create(region='US')
```

### Duplicate Handling

`delete_models_with_same_defining_fields()` checks for existing records:
- 0 matches → new record (fresh insert)
- 1 match → returns existing record (reuse)
- \>1 match → raises error (data integrity issue)

ForeignKey defining fields are handled by comparing the FK ID.

---

## 7. Permissions & Authorization

Lex has a dual permission system: **new** (`permission_*` methods returning `PermissionResult`) and **legacy** (`can_*` methods). The new system is preferred; legacy exists for backward compatibility.

### Import

```python
from lex.core.models import LexModel, UserContext, PermissionResult
```

### UserContext

Created from a Django request. Contains all auth context:

```python
@dataclass(frozen=True)
class UserContext:
    user: Any             # Django User instance
    email: str
    is_authenticated: bool
    is_superuser: bool
    groups: Set[str]      # Django group names
    keycloak_scopes: Set[str]  # UMA scopes for this resource
```

### PermissionResult

Returned by permission methods. Supports field-level control:

```python
PermissionResult.allow_all(reason="Admin access")
PermissionResult.allow_fields({'name', 'price'}, reason="Limited view")
PermissionResult.allow_all_except({'salary', 'ssn'}, reason="Hide sensitive")
PermissionResult.deny(reason="No access")
```

### Permission Methods to Override

Override these on your `LexModel` subclasses:

```python
class Product(LexModel):
    name = models.CharField(max_length=200)
    internal_cost = models.DecimalField(max_digits=10, decimal_places=2)
    price = models.DecimalField(max_digits=10, decimal_places=2)

    def permission_read(self, user_context: UserContext) -> PermissionResult:
        """Which fields can this user read on this instance?"""
        if user_context.is_superuser:
            return PermissionResult.allow_all()
        if 'admin' in user_context.groups:
            return PermissionResult.allow_all()
        # Standard users: hide internal_cost
        return PermissionResult.allow_all_except({'internal_cost'})

    def permission_edit(self, user_context: UserContext) -> PermissionResult:
        """Which fields can this user edit on this instance?"""
        if user_context.is_superuser:
            return PermissionResult.allow_all()
        if 'standard' in user_context.groups:
            return PermissionResult.allow_fields({'name', 'price'})
        return PermissionResult.deny()

    def permission_export(self, user_context: UserContext) -> PermissionResult:
        """Which fields can be exported?"""
        return PermissionResult.allow_all_except({'internal_cost'})

    def permission_create(self, user_context: UserContext) -> bool:
        """Can this user create new instances?"""
        return 'admin' in user_context.groups

    def permission_delete(self, user_context: UserContext) -> bool:
        """Can this user delete instances?"""
        return user_context.is_superuser

    def permission_list(self, user_context: UserContext) -> bool:
        """Can this user see this model in the navigation tree?"""
        return True
```

### Convenience Helpers

Available on all `LexModel` instances:

```python
def permission_read(self, user_context):
    # Allow everything if superuser
    result = self.allow_all_if_superuser(user_context)
    if result: return result

    # Allow everything for users in 'admin' group
    result = self.allow_all_if_in_groups(user_context, {'admin'})
    if result: return result

    # Allow if user owns the record (via created_by field)
    result = self.allow_fields_if_owner(user_context, 'created_by',
                                         fields={'name', 'price'})
    if result: return result

    # Exclude sensitive fields
    result = self.allow_all_except_sensitive(user_context, {'ssn', 'salary'})
    if result: return result

    # Only allow public fields: {id, name, title, description, created_at, updated_at}
    return self.allow_public_fields(user_context)
```

### Default Behavior (Keycloak Fallback)

If you DO NOT override permission methods, the defaults check Keycloak scopes:

```python
# Default implementations in LexModel:
def permission_read(self, user_context):
    return PermissionResult.allow_all() if "read" in user_context.keycloak_scopes else PermissionResult.deny()

def permission_create(self, user_context):
    return "create" in user_context.keycloak_scopes
```

### Legacy Methods (Still Supported)

```python
class Product(LexModel):
    def can_read(self, request) -> set:
        """Return set of readable field names, or empty set to deny."""
        return {'name', 'price'}

    def can_edit(self, request) -> set:
        return {'name', 'price'}

    def can_create(self, request) -> bool:
        return True

    def can_delete(self, request) -> bool:
        return False

    def can_export(self, request) -> set:
        return {'name', 'price'}

    def can_list(self, request) -> bool:
        return True
```

### How Permissions Flow Through the API

1. **Navigation**: `ModelStructureObtainView` calls `permission_list()` → prunes invisible models
2. **List/Read**: `UserReadRestrictionFilterBackend` calls `permission_read()` → filters queryset
3. **Serialization**: `LexSerializer.to_representation()` calls `can_read()` → filters output fields
4. **Create**: `OneModelEntry.create()` calls `permission_create()` → allows/denies
5. **Update**: `PermissionAwareSerializerMixin.run_validation()` checks `permission_edit()` per field
6. **Delete**: `DestroyOneWithPayloadMixin.destroy()` calls `permission_delete()` → allows/denies
7. **Export**: `ModelExportView.get_exportable_fields_for_object()` calls `permission_export()`
8. **`lex_reserved_scopes`**: Every serialized record includes `{edit: [fields], delete: bool, export: bool}` for frontend UI control

---

## 8. Modification Restrictions

A separate layer for restricting CRUD operations at the general and instance level.

### Import

```python
from lex.core.mixins import AdminReportsModificationRestriction, ExampleModelModificationRestriction
from lex.core.mixins.modification_restriction import ModelModificationRestriction
```

### Creating a Custom Restriction

```python
from lex.core.mixins.modification_restriction import ModelModificationRestriction

class MyRestriction(ModelModificationRestriction):
    def can_create_in_general(self, user, violations):
        """Can any user create instances? Append to violations list for error messages."""
        if user.is_superuser:
            return True
        violations.append("Only superusers can create records")
        return False

    def can_read_in_general(self, user, violations):
        return True

    def can_modify_in_general(self, user, violations):
        return user.is_staff

    def can_delete_in_general(self, user, violations):
        return user.is_superuser

    def can_be_read(self, instance, user, violations):
        """Instance-level read check."""
        return True

    def can_be_modified(self, instance, user, violations, request_data=None):
        """Instance-level modify check. Called on the OLD instance before update."""
        return True

    def can_be_deleted(self, instance, user, violations):
        return True
```

### Attaching to a Model

```python
class Product(LexModel):
    name = models.CharField(max_length=200)
    modification_restriction = MyRestriction()
```

### Built-in Restrictions

| Class | Behavior |
|-------|----------|
| `AdminReportsModificationRestriction` | Read-only: all reads allowed, all writes denied |
| `ExampleModelModificationRestriction` | Skeleton: all methods pass (allow everything) |

---

## 9. Serializers — API Data Layer

Lex auto-generates DRF serializers for every registered model.

### Auto-Generated Fields

Every serialized model automatically gets:

| Field | Type | Description |
|-------|------|-------------|
| `id_field` | `SerializerMethodField` | String representation of the primary key |
| `short_description` | `SerializerMethodField` | `str(instance)` — relies on `__str__` |
| `lex_reserved_scopes` | `SerializerMethodField` | `{edit: [field_names], delete: bool, export: bool}` per record |

### Custom Serializers

You can define custom serializers on your model:

```python
from rest_framework import serializers

class ProductDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ['id', 'name', 'price', 'description', 'category']

class ProductListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ['id', 'name', 'price']

# Attach to model class
Product.api_serializers = {
    'default': ProductDetailSerializer,
    'list': ProductListSerializer,
}
```

The frontend selects a serializer via `?serializer=list` query parameter.

### How Dynamic Serializer Generation Works

1. `get_serializer_map_for_model(model_class)` checks for `model.api_serializers`
2. If present: wraps each custom serializer with `id_field`, `short_description`, `lex_reserved_scopes`
3. If absent: auto-generates a `ModelSerializer` with `fields = "__all__"` plus the three internal fields
4. The serializer map is stored on `ModelContainer.serializers_map`

### Permission-Aware Serialization

`LexSerializer.to_representation()` checks `can_read()` for every instance and filters output fields. `PermissionAwareSerializerMixin.run_validation()` checks `permission_edit()` per field before allowing writes. Both use the new `permission_*` system with fallback to legacy `can_*`.

---

## 10. Custom Fields (Bokeh, HTML, PDF, XLSX)

Custom Django model fields that signal special rendering in the frontend.

### Import

```python
from lex.api.fields import BokehField, HTMLField, PDFField, XLSXField
```

### Usage

```python
from django.db import models
from lex.core.models import LexModel
from lex.api.fields import HTMLField, PDFField, XLSXField, BokehField

class Report(LexModel):
    name = models.CharField(max_length=200)
    html_content = HTMLField()          # Renders as HTML in frontend
    pdf_file = PDFField()               # Renders PDF viewer
    excel_file = XLSXField()            # Renders download link / Excel viewer
    chart = BokehField()                # Renders Bokeh visualization
```

### XLSXField — Excel Generation

`XLSXField` has a built-in `create_excel_file_from_dfs()` method:

```python
from io import BytesIO

xlsx_field = XLSXField()
excel_bytes = xlsx_field.create_excel_file_from_dfs(
    path="reports/output.xlsx",
    data_frames=[df1, df2],
    sheet_names=["Sheet1", "Sheet2"],
    merge_cells=True,
    formats=None,
    comments=None,
    index=True,
    ranges_of_pivot_concatenation=None,
)
```

---

## 11. Filters

### Built-in Filter Backends

| Class | Purpose |
|-------|---------|
| `UserReadRestrictionFilterBackend` | Filters queryset by `permission_read` / `can_be_read` per instance |
| `ForeignKeyFilterBackend` | Filters by `activeFilterTree` JSON query param (hierarchical FK filtering) |
| `PrimaryKeyListFilterBackend` | Filters by `pks` or `ids` query param |
| `StringFilterBackend` | Full-text search via `searchParams` JSON query param |

### Filter Tree

The `FilterTreeNode` class enables hierarchical filtering through FK relationships:

```
FilterTree JSON → create_filter_queries_from_tree_paths() → Django ORM Q objects
```

---

## 12. Process Admin — Model Registration & Structure

### ProcessAdminSite (Singleton)

Central registry for all models, HTML reports, processes, and structures. Accessed via:

```python
from lex.process_admin import processAdminSite
```

### Model Registration

Models are auto-discovered and registered. You rarely need to register manually. The framework:

1. Walks your project directory
2. Finds all `models.Model` subclasses
3. Calls `processAdminSite.register(model_class)` for each
4. Connects `post_save` signal → `do_post_save` for dependency cascading
5. Registers with `django-simple-history` (unless excluded)

### ModelContainer

Wraps a Django model with metadata:

```python
container = processAdminSite.model_collection.get_container_by_id('product')
container.model_class      # → Product
container.model_id         # → 'product'
container.display_title    # → 'Product'
container.pk_name          # → 'id'
container.serializers_map  # → {'default': ProductSerializer}
container.get_modification_restriction()  # → ModelModificationRestriction instance
```

### Model Structure (YAML)

Define your UI navigation tree:

```yaml
# model_structure.yaml
model_structure:
  Sales:
    Products: product
    Categories: category
    Reports:
      Monthly: monthly_report
      Annual: annual_report
  Administration:
    Users: user

model_styling:
  product:
    verbose_name: "Product Catalog"
  category:
    verbose_name: "Product Categories"

untracked_models:
  - temporary_calculation
  - staging_data
```

### Model Structure (Python)

Alternatively, define in `_structure.py`:

```python
def get_model_structure():
    return {
        "Sales": {
            "Products": "product",
            "Categories": "category",
        },
        "Administration": {
            "Users": "user",
        }
    }

def get_model_styling():
    return {
        "product": {"verbose_name": "Product Catalog"},
    }

def get_widget_structure():
    return {}
```

### ModelProcessAdmin

Controls how a model appears in the admin:

```python
from lex.process_admin.models import ModelProcessAdmin

class ProductAdmin(ModelProcessAdmin):
    fields_not_in_table_view = ['description', 'internal_notes']
    main_field = 'name'
    allow_quick_instance_creation = True
```

The field `to_display_string` controls the string representation in the table view.

---

## 13. Audit Logging

### How It Works

Every CRUD operation via the API creates an `AuditLog` entry with an `AuditLogStatus`:

1. `AuditLogMixin` (on DRF views) wraps `perform_create`, `perform_update`, `perform_destroy`
2. Creates `AuditLog(action='create'|'update'|'delete', resource=..., payload=...)`
3. Creates `AuditLogStatus(status='pending')` linked to the audit log
4. Operation executes
5. Status updated to `'success'` or `'failure'` with error traceback

### AuditLog Model

| Field | Type | Description |
|-------|------|-------------|
| `date` | `DateTimeField(auto_now_add)` | When the action occurred |
| `author` | `CharField` | Who performed the action |
| `resource` | `CharField` | Model name |
| `action` | `CharField` | `"create"` / `"update"` / `"delete"` |
| `payload` | `JSONField` | Serialized model data |
| `calculation_id` | `TextField` | Links to calculation context |
| `calculatable_object` | `GenericForeignKey` | Points to the affected model instance |

### CalculationLog Model

| Field | Type | Description |
|-------|------|-------------|
| `calculationId` | `TextField` | Links to calculation context |
| `calculation_log` | `TextField` | Accumulated log text (Markdown) |
| `parent_log` | `ForeignKey(self)` | Hierarchical log structure |
| `audit_log` | `ForeignKey(AuditLog)` | Links to audit trail |

### Initial Data Audit Logger

For bulk data uploads:

```python
from lex.audit_logging.utils import InitialDataAuditLogger

logger = InitialDataAuditLogger()
calc_id = logger.generate_calculation_id()

# Log each operation
audit_log = logger.log_object_creation(MyModel, {'name': 'test'}, calculation_id=calc_id)
logger.mark_operation_success(audit_log)
```

---

## 14. LexLogger — Fluent Markdown Logging

A singleton fluent logger that writes Markdown-formatted calculation logs.

### Import

```python
from lex.audit_logging.handlers import LexLogger
```

### Usage

```python
logger = LexLogger()

logger.add_heading("Sales Calculation", level=1) \
      .add_text("Processing region: US") \
      .add_table(
          headers=["Product", "Q1", "Q2"],
          rows=[["Widget A", "100", "150"], ["Widget B", "200", "250"]]
      ) \
      .add_dataframe(my_dataframe) \
      .add_code("result = sum(values)", language="python") \
      .add_quote("All projections completed successfully") \
      .log()  # Flush to CalculationLog
```

### Available Methods (All Chainable)

| Method | Parameters | Description |
|--------|-----------|-------------|
| `add_raw_markdown(markdown)` | `str` | Append raw Markdown |
| `add_text(text)` | `str` | Append plain text |
| `add_heading(heading, level=1)` | `str`, `int` | Markdown heading (# to ######) |
| `add_list(items, ordered=False)` | `list`, `bool` | Bullet or numbered list |
| `add_quote(quote)` | `str` | Blockquote |
| `add_code(code, language="")` | `str`, `str` | Fenced code block |
| `add_link(text, url)` | `str`, `str` | Markdown link |
| `add_image(alt_text, url)` | `str`, `str` | Markdown image |
| `add_horizontal_rule()` | — | `---` separator |
| `add_table(headers, rows)` | `list`, `list[list]` | Markdown table |
| `add_dataframe(df)` | `pd.DataFrame` | DataFrame as Markdown table |
| `log()` | — | Flush content to `CalculationLog.log()` and reset |

### Logging Context

LexLogger writes to `CalculationLog.log()`, which requires an active `operation_context` (set automatically during API operations and Celery tasks). For custom contexts:

```python
from lex.audit_logging.utils import model_logging_context

with model_logging_context(my_model_instance):
    logger.add_text("Processing...").log()
```

---

## 15. Celery Integration — Async Task Dispatch

### Configuration

Set in `.env` or environment:

```env
CELERY_ACTIVE=true
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=db+postgresql://user:pass@localhost/dbname
CELERY_TASK_TIMEOUT=3600
CELERY_MAX_RETRIES=3
CELERY_RETRY_DELAY=5
```

### Creating Custom Celery Tasks

```python
from lex.lex_app.celery_tasks import lex_shared_task

@lex_shared_task(name="my_custom_task")
def my_task(arg1, arg2):
    """Your task logic. Automatically wrapped with CeleryCalculationContext."""
    return process(arg1, arg2)
```

### RunInCelery — Selective Task Dispatch

```python
from lex.lex_app.celery_tasks import RunInCelery

# Only dispatch specific tasks to Celery
with RunInCelery(include_tasks=['calc_and_save']):
    model.save()  # This will dispatch to Celery

# Exclude specific tasks from Celery
with RunInCelery(exclude_tasks=['my_task']):
    model.save()
```

### UnblockCelery — Force Async Dispatch

```python
from lex.lex_app.celery_tasks import UnblockCelery

with UnblockCelery(force_tasks=['calc_and_save']):
    # Forces Celery dispatch even when normally sync
    model.save()
```

### CeleryTaskDispatcher

For `CalculatedModelMixin`, the framework uses `CeleryTaskDispatcher` internally:

1. Groups models by `parallelizable_fields`
2. Dispatches each group as a Celery task
3. Falls back to sync on per-group failure
4. Falls back to full sync on complete failure

### Multi-Layer Fallback

```
1. Try Celery dispatch per group
2. Single group fails → sync fallback for that group
3. ResultSet monitoring fails → sync fallback for all groups
4. Complete fallback fails → raises CeleryDispatchError
```

---

## 16. Transactions & Deferred Recalculation

### `as_transaction` Decorator

Wraps a function in a database transaction with deferred recalculation:

```python
from lex.core.transactions import as_transaction

@as_transaction
def bulk_update_prices(products, new_prices):
    """All dependent model recalculations happen ONCE on commit, not per-save."""
    for product, price in zip(products, new_prices):
        product.price = price
        product.save()
    # Dependent models recalculate here, after all saves
```

### How It Works

1. Begins `transaction.atomic()` block
2. Redirects post-save behavior to `ObjectsToRecalculateStore.insert()` (deferred)
3. Registers `do_recalculations` as `on_commit` callback
4. Your function executes — dependent objects are collected, not recalculated
5. On commit: all collected dependent objects are recalculated once
6. Resets post-save behavior

### `ObjectsToRecalculateStore`

Singleton deduplicating store:

```python
from lex.core.calculated_updates import ObjectsToRecalculateStore

# Insert manually (usually done by framework)
ObjectsToRecalculateStore.insert(my_dependent_model)

# Trigger all deferred recalculations (usually done by framework)
ObjectsToRecalculateStore.do_recalculations()
```

---

## 17. Signals & Dependency Cascade

### How Dependencies Cascade

When a model is saved:

```
Model.save()
  → Django post_save signal
  → do_post_save()
  → CalculatedModelUpdateHandler.register_save(instance)
  → Finds dependent entries via get_dependent_entries()
  → Filters for CalculatedModelMixin subclasses
  → For each dependent: post_save_behaviour(dependent)
    → Default: calc_and_save() → immediate recalculation
    → Transaction mode: ObjectsToRecalculateStore.insert() → deferred
```

### Custom Signal

```python
from lex.core.signals import custom_post_save

# Send custom post-save signal
custom_post_save.send(sender=MyModel, instance=my_instance)
```

### WebSocket Status Updates

```python
from lex.core.signals import update_calculation_status

# Manually trigger status update notification
update_calculation_status(my_calculation_model_instance)
# Sends to "update_calculation_status" WebSocket group
```

---

## 18. WebSocket Consumers — Real-Time Updates

### Available WebSocket Endpoints

| Path | Consumer | Group | Purpose |
|------|----------|-------|---------|
| `ws/health` | `BackendHealthConsumer` | — | Health check (echoes `{"status": "Healthy :)"}`) |
| `ws/calculations` | `CalculationsConsumer` | `"calculations"` | Calculation start/notification events |
| `ws/calculation_logs/<calculationId>` | `CalculationLogConsumer` | Per-calculation | Real-time log streaming |
| `ws/calculation_status_update` | `UpdateCalculationStatusConsumer` | `"update_calculation_status"` | Status transitions (in_progress/success/error) |
| `ws/logs` | `LogConsumer` | `"log_group"` | General application logs |

### Sending WebSocket Messages (Server-Side)

```python
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

channel_layer = get_channel_layer()

# Send calculation notification
async_to_sync(channel_layer.group_send)(
    "calculations",
    {"type": "calculation_notification", "payload": {"model": "report", "id": 123}}
)

# Send log message
async_to_sync(channel_layer.group_send)(
    "log_group",
    {"type": "log_message", "id": "...", "level": "INFO", "message": "Processing..."}
)
```

---

## 19. Authentication & Keycloak

### User & Profile

Django's `User` is extended with a one-to-one `Profile`:

```python
# Access profile
user.profile.uma_permissions  # JSON list of Keycloak UMA permissions
```

### Three-Tier RBAC

| Role | Description |
|------|-------------|
| `admin` | Full access to all operations |
| `standard` | Standard CRUD operations |
| `view-only` | Read-only access |

Roles map to Django Groups and Keycloak policies.

### Six Permission Scopes

Applied per Django model as Keycloak UMA scopes:

| Scope | Maps to |
|-------|---------|
| `list` | `permission_list()` / `can_list()` |
| `read` | `permission_read()` / `can_read()` |
| `create` | `permission_create()` / `can_create()` |
| `edit` | `permission_edit()` / `can_edit()` |
| `delete` | `permission_delete()` / `can_delete()` |
| `export` | `permission_export()` / `can_export()` |

### Environment Variables

```env
KEYCLOAK_URL=https://auth.example.com
KEYCLOAK_REALM=my-realm
OIDC_RP_CLIENT_ID=my-client-id
OIDC_RP_CLIENT_SECRET=my-client-secret
OIDC_RP_CLIENT_UUID=my-client-uuid
```

### KeycloakManager

Singleton managing Keycloak lifecycle:

```python
from lex.api.views.authentication.KeycloakManager import KeycloakManager

km = KeycloakManager()
km.setup_django_model_permissions_scope_based()  # Register all models as UMA resources
```

### Authentication Settings File

Create `_authentication_settings.py` in your project:

```python
initial_data_load = True  # Load initial data on startup
```

---

## 20. Streamlit Integration

### How It Works

1. Lex embeds Streamlit via an iframe in `HTMLReport`
2. A Starlette proxy (`proxy.py`) handles OIDC auth and injects user identity headers
3. `streamlit_app.py` reads headers, authenticates user, and renders your pages

### Creating Streamlit Pages

In your project, create `_streamlit_structure.py`:

```python
import streamlit as st

def main():
    st.title("My Dashboard")
    st.write("Welcome to the application")
    
    page = st.sidebar.selectbox("Page", ["Dashboard", "Reports"])
    
    if page == "Dashboard":
        show_dashboard()
    elif page == "Reports":
        show_reports()

def show_dashboard():
    st.header("Dashboard")
    # Your dashboard logic

def show_reports():
    st.header("Reports")
    # Your reports logic
```

### Model-Level Streamlit

Override on your models:

```python
class Product(LexModel):
    name = models.CharField(max_length=200)
    price = models.DecimalField(max_digits=10, decimal_places=2)

    def streamlit_main(self, user=None):
        """Instance-level: ?model=product&pk=123"""
        import streamlit as st
        st.write(f"Product: {self.name}, Price: ${self.price}")

    @classmethod
    def streamlit_class_main(cls):
        """Class-level: ?model=product"""
        import streamlit as st
        import pandas as pd
        df = pd.DataFrame(cls.objects.values('name', 'price'))
        st.dataframe(df)
```

### Accessing User in Streamlit

```python
import streamlit as st

user_info = st.session_state.get('user_info', {})
user_email = user_info.get('email', 'unknown')
user_id = user_info.get('sub', '')
permissions = st.session_state.get('permissions', {})
access_token = st.session_state.get('access_token', '')
```

---

## 21. HTMLReport — Custom HTML Views

### Import

```python
from lex.core.models import HTMLReport
```

### Usage

```python
from lex.core.models import HTMLReport

class SalesDashboard(HTMLReport):
    def get_html(self, user):
        """Return HTML string to render in the frontend."""
        return """
        <div>
            <h1>Sales Dashboard</h1>
            <p>Welcome, user!</p>
            <div id="chart-container"></div>
        </div>
        """
```

HTMLReports are registered via `processAdminSite.registerHTMLReport(name, report_instance)` during auto-discovery.

---

## 22. Process — Abstract Workflow Definition

### Import

```python
from lex.core.models import Process
```

### Usage

```python
from lex.core.models import Process

class DataPipeline(Process):
    def get_structure(self):
        """Return the process structure for the frontend."""
        return {
            "steps": [
                {"name": "Upload Data", "model": "raw_data"},
                {"name": "Validate", "model": "validated_data"},
                {"name": "Calculate", "model": "results"},
            ]
        }
```

---

## 23. CLI Commands

### `lex` CLI

```bash
# Setup project (no Django bootstrap)
lex setup

# Initialize (migrations + cache)
lex init

# Start the server (uvicorn)
lex start --reload --loop asyncio lex_app.asgi:application

# Run Streamlit
lex streamlit run streamlit_app.py

# Start Celery worker
lex celery worker -A lex_app -l info

# Run any Django management command
lex makemigrations
lex migrate
lex createsuperuser
lex shell
```

### Management Commands

| Command | Description |
|---------|-------------|
| `lex Init` | Full initialization: Keycloak setup + migrations + model sync |
| `lex Init2` | Simplified: migrations + Keycloak sync (no credential bootstrap) |
| `lex makemigrations` | Create Django migrations |
| `lex migrate` | Apply migrations |
| `lex createprofiles` | Create Profile for each User missing one |
| `lex register_keycloak_resources` | Register all models as Keycloak UMA resources |
| `lex delete_keycloak_resources` | Delete all Keycloak resources for models |
| `lex keycloak_backup` | Backup/restore Keycloak auth config |
| `lex check_history_tracking` | Show which models have history tracking |
| `lex detect_changes` | Detect pending model changes (add/delete/rename) |
| `lex bootstrap_callback_server` | Start HTTP server for Keycloak credential reception |

---

## 24. Settings & Environment Variables

### Key Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PROJECT_ROOT` | `cwd` | Project root directory |
| `DJANGO_SETTINGS_MODULE` | `lex_app.settings` | Django settings module |
| `LEX_ENVIRONMENT_TAG` | — | Deployment environment (affects DEBUG) |
| `DEPLOYMENT_ENVIRONMENT` | — | `PROD` / `DEV` / etc. |
| `DATABASE_DEPLOYMENT_TARGET` | `local` | `local` / `docker-compose` / `GCP` / `K8S` |
| `STORAGE_TYPE` | — | `SHAREPOINT` / `GCS` / (empty for local) |
| `CELERY_ACTIVE` | `false` | Enable Celery task dispatch |
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` | Redis broker URL |
| `CELERY_RESULT_BACKEND` | — | PostgreSQL URL for results |
| `CELERY_TASK_TIMEOUT` | — | Task timeout in seconds |
| `KEYCLOAK_URL` | — | Keycloak server URL |
| `KEYCLOAK_REALM` | — | Keycloak realm name |
| `OIDC_RP_CLIENT_ID` | — | OIDC client ID |
| `OIDC_RP_CLIENT_SECRET` | — | OIDC client secret |
| `OIDC_RP_CLIENT_UUID` | — | OIDC client UUID |
| `STREAMLIT_URL` | `http://localhost:8501` | Streamlit app URL |
| `INITIAL_DATA_AUDIT_LOGGING` | `false` | Enable audit logging for initial data |
| `INITIAL_DATA_AUDIT_BATCH_SIZE` | `100` | Batch size for audit logging |
| `CALLED_FROM_START_COMMAND` | — | Set by `lex start` |
| `IS_RUNNING_IN_CELERY` | — | Set in Celery workers |
| `SENDGRID_API_KEY` | — | SendGrid email API key |

### Database Configurations

| Target | Backend |
|--------|---------|
| `local` | SQLite (`db.sqlite3`) |
| `docker-compose` | PostgreSQL (`postgres:5432`) |
| `GCP` | PostgreSQL (via env vars) |
| `K8S` | PostgreSQL (via env vars) |

### Cache Backends

| Name | Backend |
|------|---------|
| `default` | `DatabaseCache` |
| `oidc` | `DatabaseCache` |
| `redis` | `RedisCache` (when available) |
| `local` | `LocMemCache` |

---

## 25. Import System — Module Aliasing

Lex provides a custom import system that allows short-form imports in user projects.

### How It Works

- `ModuleAliasingFinder` is installed on `sys.meta_path`
- Allows importing models using short names: `from Folder1.Object1 import MyModel`
- Internally maps to the canonical name: `from my_project.Folder1.Object1 import MyModel`
- `ModelAwareLoader` prevents Django model re-registration during custom imports

### For Users

You can import your models using either form:

```python
# Short form (via module aliasing)
from module_a.models import Product

# Long form (canonical)
from my_project.module_a.models import Product
```

Both resolve to the same class. Lex core modules (`lex.*`) are excluded from aliasing.

---

## 26. Utilities & Decorators

### LexSingleton

```python
from lex.utilities.decorators import LexSingleton

@LexSingleton
class MyService:
    def __init__(self):
        self.data = []

# Always returns the same instance
service1 = MyService()
service2 = MyService()
assert service1 is service2
```

### LexInjector

```python
from lex.utilities.decorators.injector import LexInjector

@LexInjector
class MyDependency:
    _is_singleton = True
```

### OperationContext

```python
from lex.api.utils.context import OperationContext

with OperationContext(request=request, calculation_id="calc-123"):
    # operation_context is now set with UUID operation_id
    # Available throughout the call stack via contextvars
    do_work()
```

### TokenContext

```python
from lex.authentication.utils.token_context import TokenContext

with TokenContext(access_token="eyJ..."):
    token = TokenContext.get_access_token()
```

### model_logging_context

```python
from lex.audit_logging.utils import model_logging_context

with model_logging_context(my_model_instance):
    # All CalculationLog entries will link to this instance
    LexLogger().add_text("Processing...").log()
```

---

## 27. Common Patterns & Conventions

### Model Hierarchy

```
django.db.models.Model
├── LifecycleModel (django-lifecycle)
│   └── LexModel (lex.core.models.base)
│       ├── CalculationModel (status-tracked calculations)
│       └── CalculatedModelMixin (combinatorial + parallel)
├── Process (abstract workflow)
└── HTMLReport (custom HTML views)
```

### Permission Check Fallback Chain

Every permission check follows this pattern:

```
1. Try new system: permission_read(UserContext) → PermissionResult
2. If not overridden: try legacy: can_read(request) → Set[str]
3. If not overridden: try Keycloak scopes
4. Default: allow (for most operations)
```

### Naming Conventions

| Pattern | Convention |
|---------|-----------|
| Model names | PascalCase, singular: `Product`, `SalesProjection` |
| Model `model_id` | Lowercase model_name: `product`, `salesprojection` |
| Table view | Uses `verbose_name` from Meta |
| `__str__` | Should return a human-readable string — used as `short_description` in API |
| `modification_restriction` | Class attribute on the model |
| `api_serializers` | Dict attribute on the model: `{'default': Serializer, 'list': Serializer}` |
| `defining_fields` | List of field names for `CalculatedModelMixin` |
| `parallelizable_fields` | Subset of `defining_fields` for Celery grouping |

### Error Handling

| Exception | When | Module |
|-----------|------|--------|
| `ValidationError` | Pre/post validation failures with rollback | `lex.core.exceptions` |
| `CalculatedModelError` | Base for all calculation errors | `lex.core.exceptions` |
| `ModelCreationError` | Model creation failures in calculated pipeline | `lex.core.exceptions` |
| `ModelCombinationError` | Field combination/expansion failures | `lex.core.exceptions` |
| `ModelClusteringError` | Clustering/hierarchy failures | `lex.core.exceptions` |
| `CeleryDispatchError` | Celery task dispatch/result failures | `lex.core.exceptions` |
| `ContextResolutionError` | Logging context resolution failures | `lex.audit_logging.utils` |
| `CacheOperationError` | Redis cache operation failures | `lex.audit_logging.utils` |

### Graceful Degradation

The framework never crashes on infrastructure failures:
- **Cache unavailable**: Operations continue without caching; returns `False`/`None`
- **WebSocket unavailable**: Notifications silently fail; returns `False`
- **Celery unavailable**: Falls back to synchronous processing
- **Keycloak unavailable**: Falls back to Django auth

---

## 28. Quick Reference — What to Import From Where

### Core Models

```python
from lex.core.models import LexModel, CalculationModel, HTMLReport, Process
from lex.core.models import UserContext, PermissionResult
```

### Mixins

```python
from lex.core.mixins import CalculatedModelMixin
from lex.core.mixins import AdminReportsModificationRestriction
from lex.core.mixins.modification_restriction import ModelModificationRestriction
```

### Custom Fields

```python
from lex.api.fields import BokehField, HTMLField, PDFField, XLSXField
```

### Transactions

```python
from lex.core.transactions import as_transaction
```

### Celery

```python
from lex.lex_app.celery_tasks import lex_shared_task, RunInCelery, UnblockCelery
```

### Logging

```python
from lex.audit_logging.handlers import LexLogger
from lex.audit_logging.utils import model_logging_context
```

### Process Admin

```python
from lex.process_admin import processAdminSite, ModelRegistration
from lex.process_admin.models import ModelProcessAdmin
```

### Authentication

```python
from lex.authentication.models import Profile
from lex.authentication.utils.token_context import TokenContext
```

### Context

```python
from lex.api.utils.context import OperationContext, operation_context
```

### Decorators

```python
from lex.utilities.decorators import LexSingleton
```

### Exceptions

```python
from lex.core.exceptions import (
    ValidationError, CalculatedModelError, ModelCreationError,
    ModelCombinationError, ModelClusteringError, CeleryDispatchError
)
```

---

## 29. Complete Code Examples

### Example 1: Simple Data Model with Permissions

```python
from django.db import models
from lex.core.models import LexModel, UserContext, PermissionResult

class Customer(LexModel):
    name = models.CharField(max_length=200)
    email = models.EmailField()
    phone = models.CharField(max_length=20, blank=True)
    internal_notes = models.TextField(blank=True)
    credit_limit = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        verbose_name = "Customer"
        verbose_name_plural = "Customers"

    def __str__(self):
        return f"{self.name} ({self.email})"

    def permission_read(self, user_context):
        result = self.allow_all_if_superuser(user_context)
        if result:
            return result
        if 'admin' in user_context.groups:
            return PermissionResult.allow_all()
        return PermissionResult.allow_all_except(
            {'internal_notes', 'credit_limit'},
            reason="Sensitive fields hidden for standard users"
        )

    def permission_edit(self, user_context):
        if user_context.is_superuser:
            return PermissionResult.allow_all()
        if 'admin' in user_context.groups:
            return PermissionResult.allow_all_except({'credit_limit'})
        return PermissionResult.allow_fields({'name', 'email', 'phone'})

    def permission_create(self, user_context):
        return 'admin' in user_context.groups or user_context.is_superuser

    def permission_delete(self, user_context):
        return user_context.is_superuser
```

### Example 2: Calculation Model

```python
from django.db import models
from lex.core.models import CalculationModel
from lex.audit_logging.handlers import LexLogger

class FinancialReport(CalculationModel):
    name = models.CharField(max_length=200)
    year = models.IntegerField()
    quarter = models.IntegerField()
    revenue = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    expenses = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    profit = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    class Meta:
        verbose_name = "Financial Report"

    def __str__(self):
        return f"{self.name} - Q{self.quarter} {self.year}"

    def calculate(self):
        """Triggered when is_calculated becomes 'In Progress'."""
        logger = LexLogger()
        logger.add_heading(f"Calculating {self.name}").log()

        # Fetch data from other models
        transactions = Transaction.objects.filter(
            year=self.year, quarter=self.quarter
        )
        self.revenue = transactions.filter(type='revenue').aggregate(
            total=models.Sum('amount')
        )['total'] or 0
        self.expenses = transactions.filter(type='expense').aggregate(
            total=models.Sum('amount')
        )['total'] or 0
        self.profit = self.revenue - self.expenses

        logger.add_table(
            headers=["Metric", "Value"],
            rows=[
                ["Revenue", f"${self.revenue:,.2f}"],
                ["Expenses", f"${self.expenses:,.2f}"],
                ["Profit", f"${self.profit:,.2f}"],
            ]
        ).log()

        self.save()
```

### Example 3: Combinatorial Calculated Model

```python
from django.db import models
from lex.core.mixins import CalculatedModelMixin
from lex.audit_logging.handlers import LexLogger

class SalesForecast(CalculatedModelMixin):
    region = models.ForeignKey('Region', on_delete=models.CASCADE)
    product = models.ForeignKey('Product', on_delete=models.CASCADE)
    year = models.IntegerField()
    forecast_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    defining_fields = ['region', 'product', 'year']
    parallelizable_fields = ['region']

    class Meta:
        verbose_name = "Sales Forecast"

    def __str__(self):
        return f"Forecast: {self.region} / {self.product} / {self.year}"

    def get_selected_key_list(self, key):
        if key == 'region':
            return list(Region.objects.all())
        elif key == 'product':
            return list(Product.objects.all())
        elif key == 'year':
            return [2024, 2025, 2026]
        return []

    def calculate(self):
        logger = LexLogger()
        logger.add_text(f"Forecasting: {self.region} / {self.product} / {self.year}").log()

        historical = SalesData.objects.filter(
            region=self.region, product=self.product
        ).aggregate(avg=models.Avg('amount'))['avg'] or 0

        self.forecast_amount = historical * 1.05  # 5% growth
        self.save()
```

### Example 4: Model with Modification Restrictions and Custom Serializer

```python
from django.db import models
from rest_framework import serializers
from lex.core.models import LexModel
from lex.core.mixins.modification_restriction import ModelModificationRestriction

class AuditTrailRestriction(ModelModificationRestriction):
    def can_create_in_general(self, user, violations):
        return False  # System-generated only

    def can_modify_in_general(self, user, violations):
        return False  # Immutable

    def can_delete_in_general(self, user, violations):
        return user.is_superuser

class SystemEvent(LexModel):
    timestamp = models.DateTimeField(auto_now_add=True)
    event_type = models.CharField(max_length=50)
    source = models.CharField(max_length=100)
    details = models.JSONField(default=dict)

    modification_restriction = AuditTrailRestriction()

    class Meta:
        verbose_name = "System Event"
        ordering = ['-timestamp']

    def __str__(self):
        return f"[{self.event_type}] {self.source} at {self.timestamp}"

# Custom serializer
class SystemEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = SystemEvent
        fields = ['id', 'timestamp', 'event_type', 'source', 'details']

SystemEvent.api_serializers = {'default': SystemEventSerializer}
```

### Example 5: Using Transactions for Batch Updates

```python
from lex.core.transactions import as_transaction

@as_transaction
def recalculate_all_forecasts(year):
    """
    Update all forecasts for a given year.
    Dependent recalculations happen ONCE after all saves.
    """
    forecasts = SalesForecast.objects.filter(year=year)
    for forecast in forecasts:
        historical = SalesData.objects.filter(
            region=forecast.region,
            product=forecast.product
        ).aggregate(avg=models.Avg('amount'))['avg'] or 0
        forecast.forecast_amount = historical * 1.05
        forecast.save()
    # All dependent models recalculate here, on commit
```

### Example 6: Custom Celery Task

```python
from lex.lex_app.celery_tasks import lex_shared_task
from lex.audit_logging.handlers import LexLogger

@lex_shared_task(name="send_weekly_report")
def send_weekly_report(report_id):
    """Custom Celery task with automatic context management."""
    from myproject.models import WeeklyReport
    
    report = WeeklyReport.objects.get(pk=report_id)
    logger = LexLogger()
    logger.add_heading("Generating Weekly Report").log()
    
    # Your logic here
    report.generate_pdf()
    report.send_email()
    
    logger.add_text("Report sent successfully").log()
    return {"status": "sent", "report_id": report_id}
```

### Example 7: Model Structure YAML

```yaml
model_structure:
  Operations:
    Customers: customer
    Products: product
    Orders:
      Active Orders: order
      Order History: orderhistory
  Analytics:
    Forecasts: salesforecast
    Reports: financialreport
  System:
    Events: systemevent

model_styling:
  customer:
    verbose_name: "Customer Management"
  salesforecast:
    verbose_name: "Sales Forecasting"

untracked_models:
  - temporarycalculation
```

### Example 8: HTMLReport

```python
from lex.core.models import HTMLReport
import pandas as pd

class SalesSummary(HTMLReport):
    def get_html(self, user):
        from myproject.models import Order
        
        orders = Order.objects.values('product__name').annotate(
            total=models.Sum('amount'),
            count=models.Count('id')
        )
        
        df = pd.DataFrame(orders)
        table_html = df.to_html(classes='table table-striped', index=False)
        
        return f"""
        <div style="padding: 20px;">
            <h1>Sales Summary</h1>
            <p>Generated for: {user}</p>
            {table_html}
        </div>
        """
```

---

## API Endpoints Reference

All endpoints are prefixed with the process admin URL prefix.

### Core

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `api/model-structure` | Navigation tree (permission-filtered) |
| GET | `api/<model>/file-download` | Download file from model instance |
| POST | `api/<model>/export` | Export model data to Excel |
| GET | `api/htmlreport/<name>` | Get HTML report content |
| GET | `api/process/<name>` | Get process structure |

### CRUD

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `api/model_entries/<model>/list` | List all entries (paginated) |
| GET | `api/model_entries/<model>/one/<pk>` | Get single entry |
| POST | `api/model_entries/<model>/one` | Create entry |
| PUT/PATCH | `api/model_entries/<model>/one/<pk>` | Update entry |
| DELETE | `api/model_entries/<model>/one/<pk>` | Delete entry |
| GET | `api/model_entries/<model>/many` | Bulk read |
| PATCH | `api/model_entries/<model>/many` | Bulk update |
| DELETE | `api/model_entries/<model>/many` | Bulk delete |

### Model Info

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `api/model_info/<model>/fields` | Field metadata for a model |
| GET | `api/<model>/model-permissions` | Permission restrictions for current user |
| GET | `api/widget_structure` | Widget structure |

### Authentication

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `api/user/` | Current user info (OIDC userinfo) |
| GET | `api/user_permissions/` | Current user permissions (ra-rbac format) |
| POST | `api/auth/streamlit-token/` | Get/generate Streamlit JWT |

### Other

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `api/global-search/<query>` | Full-text search across all models |
| GET | `api/project-info` | Project name, branch, environment |
| GET | `api/init-calculation-logs` | Get calculation logs from cache |
| POST | `api/clean-calculations` | Clean stale calculation records |
| GET | `api/calculationlog/tree/` | Calculation log tree structure |
| GET | `api/download-pdf/<pk>/` | Download calculation log as PDF |
| GET | `health` | Health check endpoint |

---

## Summary for AI Assistant

When helping developers build Lex projects:

1. **Models MUST extend `LexModel`** (or `CalculationModel` / `CalculatedModelMixin` for calculation features)
2. **Models are auto-discovered** — just create `.py` files with model classes
3. **Permissions use `permission_*` methods** returning `PermissionResult` — the new system. Legacy `can_*` still works.
4. **`__str__` is important** — used as `short_description` in the API
5. **`defining_fields` creates unique constraints** automatically via metaclass
6. **`get_selected_key_list()` drives combinatorial expansion** — return all valid values for each defining field
7. **`calculate()` is the main calculation entry point** — triggered when `is_calculated` becomes `"In Progress"`
8. **`as_transaction` defers recalculations** — use for batch operations
9. **LexLogger is a singleton** — `.log()` flushes content; methods are chainable
10. **YAML structure defines UI navigation** — leaf values are `model_id` (lowercase `model_name`)
11. **Celery is optional** — framework falls back to sync processing seamlessly
12. **All field types work** — plus custom `HTMLField`, `PDFField`, `XLSXField`, `BokehField` for special rendering
