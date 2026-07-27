# Permissions & Authorization

Search keywords: permission_read, UserContext, PermissionResult, Keycloak scopes, field-level access, modification restriction

## Scope

- Field-level and action-level authorization in Lex
- Permission object model and fallback behavior
- Integration with request context and Keycloak
- Record-level modification guardrails

## Key Points

- Core abstractions: `UserContext` (who/what scopes) and `PermissionResult` (what fields/actions are allowed).
- Override `permission_read/edit/export/create/delete/list` on models for domain-specific policy.
- Default fallback chain uses Keycloak scope-based checks when custom logic is not provided.
- Legacy `can_*` methods and `ModificationRestriction` classes are deprecated. Use `permission_*()` methods directly.

## UserContext

A frozen dataclass passed to every permission method:

```python
from lex.core.models.LexModel import LexModel, UserContext, PermissionResult

@dataclass(frozen=True)
class UserContext:
    user: Any              # Django User object
    email: str             # User's email address
    is_authenticated: bool # Is user logged in?
    is_superuser: bool     # Is user a superuser?
    groups: Set[str]       # Group names, e.g. {'admin', 'finance'}
    keycloak_scopes: Set[str]  # Keycloak permission scopes
    client_roles: FrozenSet[str]  # Keycloak client roles
```

Constructed automatically from the Django request via `UserContext.from_request(request, instance)`.

## PermissionResult

Returned by field-level permission methods. Use factory methods:

| Factory | What It Does |
|---|---|
| `PermissionResult.allow_all()` | Grant access to every field |
| `PermissionResult.allow_fields({"name", "email"})` | Grant access to specific fields only |
| `PermissionResult.allow_all_except({"salary"})` | Exclude specific fields |
| `PermissionResult.deny("reason")` | Deny access entirely |
| `PermissionResult.deny_all()` | Explicit alias for `deny()` |

## Permission Methods

### Field-Level (return `PermissionResult`)

| Method | Purpose |
|---|---|
| `permission_read(user_context)` | Which fields the user can view |
| `permission_edit(user_context)` | Which fields the user can modify |
| `permission_export(user_context)` | Which fields appear in exports |

### Action-Level (return `bool`)

| Method | Purpose |
|---|---|
| `permission_create(user_context)` | Can this user create new instances? |
| `permission_delete(user_context)` | Can this user delete this instance? |
| `permission_list(user_context)` | Can this user list instances of this model? |

## Example: Basic Permissions

```python
class MyModel(LexModel):
    name = models.CharField(max_length=100)
    sensitive_field = models.CharField(max_length=100)
    owner_email = models.EmailField()

    def permission_read(self, user_context: UserContext) -> PermissionResult:
        if user_context.is_superuser or 'admin' in user_context.groups:
            return PermissionResult.allow_all()
        return PermissionResult.allow_all_except({'sensitive_field'})

    def permission_delete(self, user_context: UserContext) -> bool:
        return user_context.is_superuser or self.owner_email == user_context.email
```

## Example: Record Ownership

```python
class ExpenseReport(LexModel):
    employee_email = models.EmailField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    def permission_read(self, user_context: UserContext) -> PermissionResult:
        if 'finance_manager' in user_context.groups:
            return PermissionResult.allow_all()
        if self.employee_email == user_context.email:
            return PermissionResult.allow_all()
        return PermissionResult.allow_fields(set())  # row hidden
```

## Keycloak Integration

By default, permission methods fall back to Keycloak scopes. After `lex Init`, models are synced to Keycloak as Resources with permission methods registered as Scopes. You can use scopes directly:

```python
def permission_read(self, user_context: UserContext) -> PermissionResult:
    if 'read' in user_context.keycloak_scopes:
        return PermissionResult.allow_all()
    return PermissionResult.deny("No read permission in Keycloak")
```

Permissions are enforced across 6 scopes: `read`, `edit`, `export`, `create`, `delete`, `list`. These are synced to Keycloak on `lex Init`, enabling centralized policy management at [Excellence Cloud](https://excellence-cloud.de).

## Convenience Helpers

`LexModel` includes helper methods to reduce boilerplate:

```python
def permission_read(self, user_context):
    result = self.allow_all_if_superuser(user_context)
    if result:
        return result

    result = self.allow_all_if_in_groups(user_context, {"admin", "manager"})
    if result:
        return result

    result = self.allow_fields_if_owner(
        user_context,
        owner_field="created_by",
        excluded_fields={"internal_notes"},
    )
    if result:
        return result

    return self.keycloak_fallback(user_context, "read")
```

| Helper | Returns |
|---|---|
| `allow_all_if_superuser(user_context)` | `PermissionResult.allow_all()` if superuser, else `None` |
| `allow_all_if_in_groups(user_context, groups)` | `PermissionResult.allow_all()` if user in any group |
| `allow_fields_if_owner(user_context, owner_field, ...)` | Grants access if the user owns the record |
| `keycloak_fallback(user_context, scope)` | Falls back to Keycloak scope check |

## Where to Expand

- `lex_context.md`: Permission System (Field-Level Authorization)
- `lex_context_repo.md`: Permissions & Authorization; How Permissions Flow Through the API
- `docs/_context/lex_examples/LexModelExplain.py`: concrete `UserContext` and `PermissionResult` patterns

## LLM Prompt Starters

- "Write permission methods for this model using `UserContext` and `PermissionResult` with field-level control."
- "Trace why a user can read but not edit certain fields using Lex permission fallback rules."
