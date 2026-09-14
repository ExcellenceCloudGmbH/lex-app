---
title: "LexModel Internals"
---

`LexModel` is the base class for every data model in Lex App. It extends [django-lifecycle](https://rsinger86.github.io/django-lifecycle/)'s `LifecycleModel` with automatic history tracking, permission hooks, validation hooks, and [Streamlit](https://docs.streamlit.io/) integration.

> [!tip]
> Browse the full source on [GitHub](https://github.com/ExcellenceCloudGmbH/lex-app/blob/lex-app-v2/lex/core/models/LexModel.py).

```python
from lex.core.models.LexModel import LexModel, UserContext, PermissionResult
```

## Auto-Provided Fields

Every `LexModel` subclass automatically gets these fields — you never need to declare them:

| Field | Type | Description |
|---|---|---|
| `id` | `AutoField` | Primary key (inherited from [Django](https://docs.djangoproject.com/)) |
| `created_at` | `DateTimeField` | When the record was first created (set automatically) |
| `edited_at` | `DateTimeField` | When the record was last edited (set automatically on every user edit) |
| `created_by` | `TextField` | Username of the creator (set automatically) |
| `edited_by` | `TextField` | Username of the last editor (set automatically for normal user edits; calculation-triggered framework saves don't overwrite it) |

History fields (via [django-simple-history](https://django-simple-history.readthedocs.io/)) are added transparently — you don't interact with them directly.

> [!note] Timestamps are stored and served in UTC
> `created_at` and `edited_at` are stored in UTC and served by the API with an explicit
> `Z` designator (e.g. `2026-07-14T11:43:00Z`). Parse them as UTC and convert to local
> time for display — the application already does this for you. This is the same contract
> the [[history-and-audit/bitemporal history#The REST API|`as_of` history queries]] follow.

## Lifecycle Hooks

### `pre_validation(self)`

Called **before** every save (create or update). Raise any exception to cancel the save — the record will not be written to the database.

```python
def pre_validation(self):
    if self.amount <= 0:
        raise ValueError("Amount must be positive.")
```

### `post_validation(self)`

Called **after** the save completes. Raise any exception to trigger an automatic rollback — the framework restores the record to its pre-save state.

```python
def post_validation(self):
    if self.team.budget - self.amount < 0:
        raise ValueError("This would exceed the team budget.")
```

> [!important]
> Both hooks are wired via [django-lifecycle](https://rsinger86.github.io/django-lifecycle/)'s `@hook(BEFORE_SAVE)` and `@hook(AFTER_SAVE)` decorators internally. You just override the method — no decorator needed on your side.

## Permission Methods

Lex App checks permissions automatically on every API request and frontend interaction. Override these methods on your model to customize access control.

### Field-Level Methods (return `PermissionResult`)

| Method | Default Behavior | When It's Called |
|---|---|---|
| `permission_read(user_context)` | Allow if Keycloak `read` scope | Every GET / list request |
| `permission_edit(user_context)` | Allow if Keycloak `edit` scope | Every PATCH / PUT request |
| `permission_export(user_context)` | Allow if Keycloak `export` scope | CSV / Excel export |

These return a `PermissionResult` which can grant access to all fields, specific fields, or deny access entirely.

### Action-Level Methods (return `bool`)

| Method | Default Behavior | When It's Called |
|---|---|---|
| `permission_create(user_context)` | Allow if Keycloak `create` scope | POST request |
| `permission_delete(user_context)` | Allow if Keycloak `delete` scope | DELETE request |
| `permission_list(user_context)` | Allow if Keycloak `list` scope | List / table view |

## `UserContext`

A frozen dataclass passed to every permission method. It contains everything you need to make authorization decisions.

```python
@dataclass(frozen=True)
class UserContext:
    user: Any                          # Django User instance
    email: str
    is_authenticated: bool
    is_superuser: bool
    groups: Set[str]                   # Keycloak / Django groups
    keycloak_scopes: Set[str]          # Keycloak UMA scopes for this resource
    user_permissions: tuple[...]       # Raw Keycloak UMA permissions
    client_roles: FrozenSet[str]       # Keycloak client roles
```

Constructed automatically from the Django request via `UserContext.from_request(request, instance)`.

## `PermissionResult`

A frozen dataclass returned by field-level permission methods. Use the factory methods:

| Factory | What It Does |
|---|---|
| `PermissionResult.allow_all()` | Grant access to every field |
| `PermissionResult.allow_fields({"name", "email"})` | Grant access to specific fields only |
| `PermissionResult.allow_all_except({"salary"})` | Grant access to all fields except listed ones |
| `PermissionResult.deny("reason")` | Deny access entirely |
| `PermissionResult.deny_all()` | Explicit alias for `deny()` |

## Convenience Helpers

`LexModel` includes helper methods you can call inside your permission methods to reduce boilerplate:

```python
def permission_read(self, user_context):
    # Check superuser first
    result = self.allow_all_if_superuser(user_context)
    if result:
        return result

    # Check group membership
    result = self.allow_all_if_in_groups(user_context, {"admin", "manager"})
    if result:
        return result

    # Check record ownership
    result = self.allow_fields_if_owner(
        user_context,
        owner_field="created_by",
        excluded_fields={"internal_notes"},
    )
    if result:
        return result

    # Fall back to Keycloak
    return self.keycloak_fallback(user_context, "read")
```

| Helper | Returns |
|---|---|
| `allow_all_if_superuser(user_context)` | `PermissionResult.allow_all()` if superuser, else `None` |
| `allow_all_if_in_groups(user_context, groups)` | `PermissionResult.allow_all()` if user is in any of the groups |
| `allow_fields_if_owner(user_context, owner_field, ...)` | Grants access if the user owns the record |
| `keycloak_fallback(user_context, scope)` | Falls back to Keycloak scope check |
| `allow_all_except_sensitive(user_context)` | Excludes common sensitive fields (`password`, `ssn`, etc.) |

## Streamlit Methods

| Method | Level | Description |
|---|---|---|
| `streamlit_main(self, user=None)` | Record-level | Override for per-record [Streamlit](https://docs.streamlit.io/) dashboard |
| `streamlit_class_main(cls)` | Table-level (`@classmethod`) | Override for model-wide dashboard |

> [!warning]
> Always import `streamlit` **inside** these methods, not at the top of the file. See [[start-here/tutorial/Part 5 — Streamlit Dashboards]] for details.

## History Tracking Control

| Method | What It Does |
|---|---|
| `track()` | Re-enable history tracking for this instance |
| `untrack()` | Disable history tracking for the next save |
| `save_without_historical_record()` | Save once without creating a history entry |

For bulk operations where history tracking is expensive, use `Model.objects.bulk_create(objs, skip_history=True)`.

## Change Detection & Lean Initial State

django-lifecycle powers change detection — `has_changed('field')`, `initial_value('field')`, and the conditional `@hook(when=..., has_changed=True)` forms — by keeping a snapshot of the instance's field values from when it was loaded. By default Lex App keeps a **lean** snapshot rather than a full copy of every field, which materially cuts per-row memory on large `calculate`-all runs (where every live row would otherwise carry a second full copy of itself).

The lean snapshot retains only the fields the framework can prove are needed: `edited_at`, every field named in this class's hook clauses (the `when=` / `when_any=` / `condition=` conditions, including chained `&`/`|` forms), and anything you list in `lex_initial_state_extra_fields`. Tracked values stay byte-for-byte identical to the full snapshot — the narrowing is transparent for the vast majority of models.

| Attribute | Default | What It Does |
|---|---|---|
| `lex_lean_initial_state` | `True` | When `True`, narrow the change-detection snapshot to the tracked fields above. Set `False` to restore the full per-field snapshot. |
| `lex_initial_state_extra_fields` | `()` | Field names to keep in the lean snapshot beyond what's auto-discovered. The escape hatch for models that call `has_changed` / `initial_value` imperatively. |

> [!warning] If you query change detection on a field outside a hook clause
> Calling `has_changed('x')` or `initial_value('x')` on a field that isn't tracked by a `@hook` clause won't be auto-discovered. Either declare it:
> ```python
> class MyModel(LexModel):
>     lex_initial_state_extra_fields = ("x",)
> ```
> or opt the whole model out with `lex_lean_initial_state = False` to restore the full snapshot.

## DateTimeField Timezone Awareness

When `USE_TZ = True`, every `DateTimeField` on a `LexModel` subclass is made timezone-aware the moment you assign a value — not only when it comes back from the database. If you assign a naive datetime, the framework automatically converts it to the project's default timezone (the `TIME_ZONE` setting), which is the same interpretation Django applies when it saves the value.

```python
from datetime import datetime

obj = MyReport()
obj.report_date = datetime(2026, 1, 15, 9, 0)  # naive in → aware out
# obj.report_date is now tz-aware (e.g. UTC+01:00 for Europe/Berlin in winter)
```

Already-aware datetimes pass through unchanged — the framework never re-zones a value you've already pinned to a timezone.

This keeps in-memory instances consistent with fetched querysets. Mixing the two — say, a freshly-created object alongside rows loaded from the database — won't produce a `TypeError: Cannot compare tz-naive and tz-aware timestamps` in pandas sorts or financial calculations.

> [!note]
> This behaviour only applies under `USE_TZ = True`. With `USE_TZ = False`, naive datetimes are stored and returned as-is and no conversion takes place.
