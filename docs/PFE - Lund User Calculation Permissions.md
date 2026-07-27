# PFE — Environment-Aware User Permissions

## Overview

PFE requires **environment-aware access control** for all models:

- **Production** — Permissions are managed entirely through **Keycloak**. The mixins
  delegate to `super()` (default `LexModel` behaviour). The client configures who
  can do what directly in their Keycloak instance.
- **Non-production** — When a production instance is cloned for testing, the Keycloak
  client cannot be switched. The mixins override permissions so that Lund users
  (`admin`, `support`) receive the same write access as customer roles.

This is implemented through **permission mixins** that override the `permission_*` methods
from `LexModel`. Everything lives in a single file (`pfe_permissions.py`) in the PFE
project. No changes to `LexModel` or `CalculationModel` are required.

---

## Environment Detection

```python
IS_PROD = True   when INSTANCE_TYPE is NOT one of: unset, "prod", "prod-like"
IS_PROD = False  when INSTANCE_TYPE is unset, "prod", or "prod-like"
```

| `INSTANCE_TYPE` value | `is_prod()` |
|---|---|
| *(unset / empty)* | `False` |
| `"prod"` | `False` |
| `"prod-like"` | `False` |
| `"production"` | `True` |
| *(anything else)* | `True` |

This ensures cloned production instances marked `"prod"` or `"prod-like"` are treated
as non-production — so Lund users can still work when testing on clones.

## User Roles

| Role | Type | Description |
|---|---|---|
| `standard` | Customer | Standard PFE customer user |
| `acp-hr` | Customer | HR customer user |
| `admin` | Lund | Lund Consulting administrator |
| `support` | Lund | Lund Consulting support staff |

Roles are sourced from `user_context.client_roles` (Keycloak client roles).

---

## Permission Mixins

Four mixin classes in `pfe_permissions.py` override the `permission_*` methods
(`permission_read`, `permission_edit`, `permission_create`, `permission_delete`).

**Production** columns show "Keycloak" — meaning `super()` is called and the
default `LexModel` Keycloak-scope logic applies.

### PeriodLevelPermissions

| IS_PROD | Read | Modify | Create | Delete |
|---|---|---|---|---|
| TRUE | Keycloak | Keycloak | Keycloak | Keycloak |
| FALSE | All users | Standard, Admin, Support | Standard, Admin, Support | Standard, Admin, Support |

### PlanLevelPermissions

| IS_PROD | Read | Modify | Create | Delete |
|---|---|---|---|---|
| TRUE | Keycloak | Keycloak | Keycloak | Keycloak |
| FALSE | All users | Standard, Admin, Support | No user | No user |

### HRCalculationPermissions

| IS_PROD | Read | Modify | Create | Delete |
|---|---|---|---|---|
| TRUE | Keycloak | Keycloak | Keycloak | Keycloak |
| FALSE | All users | Standard, HR, Admin, Support | Standard, HR, Admin, Support | Standard, HR, Admin, Support |

### ViewOnlyPermissions

| IS_PROD | Read | Modify | Create | Delete |
|---|---|---|---|---|
| TRUE | Keycloak | Keycloak | Keycloak | Keycloak |
| FALSE | All users | No user | No user | No user |

---

## Usage

### Applying a mixin to a model

Place the mixin **before** `CalculationModel` or `LexModel` in the inheritance list
so its `permission_*` methods take precedence:

```python
from pfe_permissions import PeriodLevelPermissions
from lex.core.models.CalculationModel import CalculationModel


class SomePeriodModel(PeriodLevelPermissions, CalculationModel):
    # ... fields ...
    pass
```

### Blocking calculations for Lund users (optional extra layer)

For `CalculationModel` subclasses where Lund users must be blocked from
triggering calculations in production, combine the mixin with the
`exclude_calculation_for_lund` helper:

```python
from pfe_permissions import PeriodLevelPermissions, exclude_calculation_for_lund
from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.LexModel import UserContext, PermissionResult


class SomePFECalculation(PeriodLevelPermissions, CalculationModel):
    # ... fields ...

    def permission_edit(self, user_context: UserContext) -> PermissionResult:
        result = super().permission_edit(user_context)
        return exclude_calculation_for_lund(user_context, result)
```

**How it works:**

1. User clicks **Calculate ▶️** in the frontend
2. The framework sets `is_calculated = "IN_PROGRESS"` on the record
3. `@hook(AFTER_UPDATE, condition=WhenFieldValueIs("is_calculated", IN_PROGRESS))` fires
4. `exclude_calculation_for_lund` removes `is_calculated` from the editable fields
   for Lund users in production, so the `IN_PROGRESS` write is rejected and
   `calculate_hook()` never fires.

### Calculation exclusion merging logic

| Base result | Lund + production? | Final result |
|---|---|---|
| `allow_all()` | ✅ Yes | `allow_all_except({"is_calculated"})` |
| `allow_all_except({"salary"})` | ✅ Yes | `allow_all_except({"salary", "is_calculated"})` |
| `allow_fields({"name", "is_calculated"})` | ✅ Yes | `allow_fields({"name"})` |
| `deny()` | ✅ Yes | `deny()` (unchanged) |
| *Any result* | ❌ No (non-prod or non-Lund) | Unchanged |

---

## Behavior Summary

| Scenario | Can read? | Can modify/create/delete? | Can trigger calculation? |
|---|---|---|---|
| Any user + **production** | ✅ Per Keycloak | ✅ Per Keycloak | ✅ Per Keycloak |
| Lund user + **non-production** | ✅ Yes | ✅ Same as customer roles (per mixin) | ✅ Allowed |
| Customer user + **non-production** | ✅ Yes | ✅ Per mixin rules | ✅ Per mixin rules |

## Requirements

- Set the `INSTANCE_TYPE` environment variable appropriately on each instance.
- In production, configure permissions through the Keycloak client directly.
- No changes to `LexModel` or `CalculationModel` are required.
- All permission logic lives in `pfe_permissions.py` in the PFE project.
