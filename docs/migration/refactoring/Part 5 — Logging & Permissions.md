---
title: "Part 5 — Logging & Permissions"
---

This part covers two independent systems that both changed significantly from V1: logging and permissions. You can tackle them in either order.

## Logging: `CalculationLog` → `LexLogger`

V1 used `CalculationLog.create()` for plain-text log entries. The current system uses `LexLogger`, a builder-pattern API that produces rich Markdown output with tables, headings, DataFrames, and code blocks.

### What Changes

| Aspect | V1 (`CalculationLog`) | Current (`LexLogger`) |
|---|---|---|
| API | `CalculationLog.create(text, ...)` | `LexLogger()` builder chain |
| Output | Plain text | Rich Markdown |
| Context | Manual — pass IDs | Automatic |
| Nested calculations | Not supported | Built-in hierarchy |

### Step-by-Step

#### 1. Replace Imports

```python
# Before
from generic_app.submodels.CalculationLog import CalculationLog

# After
from lex.audit_logging.handlers.LexLogger import LexLogger
```

#### 2. Replace `CalculationLog.create()` Calls

```python
# Before
CalculationLog.create(
    f"NAV calculated: {total}",
    calculation_object=self
)

# After
LexLogger().add_text(f"NAV calculated: {total}").log()
```

> [!warning]
> Always call `.log()` at the end of your chain. Without it, nothing is written to the database.

#### 3. Upgrade to Rich Formatting (Optional)

Now that you have `LexLogger`, you can upgrade plain text logs to rich Markdown:

```python
# Plain text (works, but basic)
LexLogger().add_text(f"NAV calculated: {total}").log()

# Rich formatting (much better)
LexLogger() \
    .add_heading(f"NAV Report: {self.quarter}") \
    .add_table(
        headers=["Metric", "Value"],
        rows=[
            ["Total NAV", f"€{total:,.2f}"],
            ["Investments", str(count)],
        ]
    ) \
    .log()
```

#### 4. Remove Manual Context Passing

V1 required you to pass `calculation_object=self`. `LexLogger` figures this out automatically:

```python
# Before
CalculationLog.create("Processing...", calculation_object=self, parent=parent_obj)

# After
LexLogger().add_text("Processing...").log()
# Context is resolved automatically
```

For the complete API, see [[reference/LexLogger API]].

## Permissions: `ModificationRestriction` → `permission_*` Methods

V1 used separate `ModificationRestriction` classes to define access control. The current system uses `permission_*` methods defined directly on your model.

### What Changes

| Aspect | V1 (`ModificationRestriction`) | Current (`permission_*`) |
|---|---|---|
| Location | Separate class | Methods on your model |
| User info | Raw `user` object | Clean `UserContext` dataclass |
| Granularity | Model-level | Field-level and row-level |
| Integration | Manual wiring | Automatic (Keycloak built-in) |

### Step-by-Step

#### 1. Remove the Restriction Class

```python
# Before — separate file or inside model file
class FundRestriction(ModelModificationRestriction):
    def has_permission(self, user, violations):
        if not user.is_authenticated:
            violations.append("Not authenticated")
            return False
        return True

class Fund(Model):
    modification_restriction = FundRestriction()
    # ... fields ...
```

#### 2. Add Permission Methods Directly to Your Model

```python
# After
from lex.core.models.LexModel import LexModel, UserContext, PermissionResult

class Fund(LexModel):
    # ... fields ...

    def permission_read(self, user_context: UserContext) -> PermissionResult:
        if user_context.is_superuser or 'fund_manager' in user_context.groups:
            return PermissionResult.allow_all()
        return PermissionResult.allow_all_except({'internal_notes'})

    def permission_delete(self, user_context: UserContext) -> bool:
        return user_context.is_superuser
```

#### 3. Remove the Old References

Delete the `modification_restriction = ...` line and the `ModificationRestriction` class itself.

```bash
# Find remaining references
grep -rn "ModificationRestriction\|modification_restriction" --include="*.py" .
```

### `PermissionResult` Quick Reference

| Method | Behavior |
|---|---|
| `PermissionResult.allow_all()` | Full access |
| `PermissionResult.allow_fields({'a', 'b'})` | Only these fields |
| `PermissionResult.allow_all_except({'x'})` | Everything except these |
| `PermissionResult.deny()` | No access |

For more on the permission system, see [[features/access-and-ui/permissions]].

### Sync to Keycloak

After adding permission methods, run `lex Init` to sync your models to Keycloak. You can then manage group assignments at [Excellence Cloud](https://excellence-cloud.de).

## Verify

```bash
# Should all return zero results
grep -rn "CalculationLog" --include="*.py" .
grep -rn "ModificationRestriction\|modification_restriction" --include="*.py" .
```

## Checkpoint

- [ ] All `CalculationLog.create()` calls replaced with `LexLogger()`
- [ ] Every `LexLogger` chain ends with `.log()`
- [ ] Manual context passing (`calculation_object=...`) removed
- [ ] All `ModificationRestriction` classes deleted
- [ ] `modification_restriction = ...` lines removed from models
- [ ] `permission_*` methods added directly to models
- [ ] `lex Init` run to sync permissions to Keycloak

Congratulations — your V1 project code is fully refactored! For ongoing development, explore the [[features/index|building blocks]] and the [[reference/index|reference section]].
