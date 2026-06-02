---
title: "Part 4 — Validation & Permissions"
---

In this part, you'll add two things that turn a data entry form into a real business application: **validation** to prevent bad data from being saved, and **permissions** to control who sees what. These work alongside the [[features/data-pipeline/serializers|serializer validation]] you added in Part 2 — but at the model level, so they apply regardless of how data enters the system.

## Add Pre-Validation to Expense

Open `Input/Expense.py` and add a `pre_validation` method to your `Expense` class. This runs before every save — whether the data comes from the API, the frontend, or an upload model:

```python title="Input/Expense.py"
class Expense(LexModel):
    # ... existing fields ...

    def pre_validation(self):
        """Block invalid expenses before they are saved."""
        if self.amount <= 0:
            raise ValueError("Expense amount must be positive.")

        if self.amount > 10000:
            raise ValueError(
                "Expenses over €10,000 require manual approval. "
                "Please contact the CFO."
            )
```

> [!note]
> **Serializer vs. model validation:** Your `serializers.py` validates data at the API layer — great for field formats and cross-field rules. `pre_validation()` validates at the model layer — the last line of defense before the database. Both are useful. See [[features/data-pipeline/lifecycle hooks]] for more on the validation lifecycle.

### Try It Out

1. Select **"Start"** in PyCharm → click ▶️
2. Navigate to **Expense**
3. Try to create an expense with amount **-50** → blocked
4. Try to create an expense with amount **15000** → blocked
5. Create one with amount **250** → saved

The error message appears directly in the [AG Grid](https://www.ag-grid.com/)-powered UI. No data is written to the database when validation fails.

## Add Permissions to Expense

First, update the import at the top of `Input/Expense.py`:

```python
from lex.core.models.LexModel import LexModel, UserContext, PermissionResult
```

Then add these methods to your `Expense` class:

```python title="Input/Expense.py"
class Expense(LexModel):
    # ... existing fields and pre_validation ...

    def permission_read(self, user_context: UserContext) -> PermissionResult:
        """
        - Employees see only their own expenses
        - Managers see their team's expenses
        - CFO sees everything
        """
        if user_context.is_superuser:
            return PermissionResult.allow_all()

        # CFO sees everything
        if "cfo" in user_context.groups:
            return PermissionResult.allow_all()

        # Managers see their team's expenses
        if "manager" in user_context.groups:
            if self.employee.team.manager_email == user_context.email:
                return PermissionResult.allow_all()

        # Employees see only their own
        if self.employee.email == user_context.email:
            return PermissionResult.allow_all()

        return PermissionResult.deny("You can only view your own expenses.")

    def permission_delete(self, user_context: UserContext) -> bool:
        """Only managers and CFO can delete expenses."""
        if user_context.is_superuser:
            return True
        return "manager" in user_context.groups or "cfo" in user_context.groups
```

## How Permissions Work

| User Role | Can See | Can Delete |
|---|---|---|
| **Employee** | Only their own expenses | No |
| **Manager** | Their team's expenses | Yes |
| **CFO** | All expenses across all teams | Yes |
| **Superuser** | Everything | Yes |

The permissions are checked automatically by the framework on every API request and frontend interaction. You don't need any middleware or decorators — and they integrate with the `@add_permission_checks` decorator on your [[features/data-pipeline/serializers|serializers]]. For the complete list of permission methods and convenience helpers, see [[reference/LexModel Internals]]. For more on the permission system, see [[features/access-and-ui/permissions]].

## Sync Permissions

Select **"Init"** in PyCharm → click ▶️ to sync your model permissions to [Keycloak](https://www.keycloak.org/documentation).

> [!note]- Terminal alternative
> **Linux / macOS:**
> ```bash
> set -a; source .env; set +a
> lex Init
> ```
> **Windows PowerShell:**
> ```powershell
> lex Init
> ```

## How It Looks

When **Anna** (employee, Design team) logs in, she sees only her expenses:

| Description | Amount | Category |
|---|---|---|
| Flight to Munich | €450.00 | Travel |
| Team lunch with client | €85.00 | Meals |
| Figma Annual License | €180.00 | Software |

When **Thomas** (manager, Design team) logs in, he sees all Design expenses:

| Description | Amount | Category | Employee |
|---|---|---|---|
| Flight to Munich | €450.00 | Travel | Anna Schmidt |
| Adobe Creative Suite | €720.00 | Software | Max Weber |
| Team lunch with client | €85.00 | Meals | Anna Schmidt |
| Figma Annual License | €180.00 | Software | Anna Schmidt |
| Train to Berlin | €120.00 | Travel | Max Weber |

When the **CFO** logs in, they see everything across all teams.


## Checkpoint

At this point you have:
- Model-level validation rules that block bad data
- API-level serializer validation from Part 2
- Role-based permissions (employee, manager, CFO)
- [Keycloak](https://www.keycloak.org/documentation) integration for group management

Next up: [[tutorial/Part 5 — Streamlit Dashboards|Part 5 — Streamlit Dashboards]].
