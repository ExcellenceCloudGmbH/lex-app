---
title: "Part 4 — Validation & Permissions"
aliases:
  - "tutorial/Part 4 — Validation & Permissions"
---

In this part, you'll add two things that turn a data entry form into a real business application: **validation** to prevent bad data from being saved, and **permissions** to control who sees what. These work alongside the [[model-your-data/serializers|serializer validation]] you added in Part 2 — but at the model level, so they apply regardless of how data enters the system.

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
> **Serializer vs. model validation:** Your `serializers.py` validates data at the API layer — great for field formats and cross-field rules. `pre_validation()` validates at the model layer — the last line of defense before the database. Both are useful. See [[model-your-data/lifecycle hooks]] for more on the validation lifecycle.

### Try It Out

1. Select **"Start"** in PyCharm → click ▶️
2. Navigate to **Expense**
3. Try to create an expense with amount **-50** → blocked
4. Try to create an expense with amount **15000** → blocked
5. Create one with amount **250** → saved

The error message appears directly in the [AG Grid](https://www.ag-grid.com/)-powered UI. No data is written to the database when validation fails.

![An expense of 18,500 rejected on save: the form keeps the values that were entered, no record is created, and a banner offers the failure audit for the attempt](images/tutorial/validation-rejected.png)

Two things are worth noticing in that screenshot. The form keeps what you
typed, so a rejected save is a correction rather than a re-entry. And the
banner across the top is the audit trail: the attempt was recorded even though
nothing was written, because the audit entry is created *before* the operation
runs. See [[using-the-app/record-detail/audit log tab|the Audit Log tab]].

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

The permissions are checked automatically by the framework on every API request and frontend interaction. You don't need any middleware or decorators — and they integrate with the `@add_permission_checks` decorator on your [[model-your-data/serializers|serializers]]. For the complete list of permission methods and convenience helpers, see [[reference/LexModel Internals]]. For more on the permission system, see [[access-and-dashboards/permissions]].

## Sync Permissions

Select **"Init"** in PyCharm → click ▶️ to sync your model permissions to [Keycloak](https://www.keycloak.org/documentation).

> [!note]- Terminal alternative
> **Linux / macOS:**
> ```bash
> set -a; source .env; set +a
> lex init
> ```
> **Windows PowerShell:**
> ```powershell
> lex init
> ```

## Try It Yourself

Once `permission_read` is in place, your own login can most likely read **none**
of the expenses, and the Expense grid comes up empty. Rows you may not read are
left out without a message, so an empty grid here means the rule works:

- the seed data creates employees, not logins, so your email matches no `Employee`;
- `cfo` and `manager` don't exist yet — `user_context.groups` holds the names of
  the **Django** groups the signed-in user belongs to;
- your account is not a superuser.

To see every expense, as the CFO does, put yourself in a `cfo` group. Sign in to
the app once first so your user exists, then run `lex shell` and:

```python
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

me = get_user_model().objects.get(email="you@example.com")  # the email you sign in with
cfo, _ = Group.objects.get_or_create(name="cfo")
me.groups.add(cfo)
```

Reload the Expense grid and every row is back. Keep it that way for
[[start-here/tutorial/Part 6 — History in Action|Part 6]], which edits one of
Anna's expenses.

## How It Looks

If **Anna** (employee, Design team) signed in, she would see only her expenses:

| Description | Amount | Category |
|---|---|---|
| Flight to Munich | €450.00 | Travel |
| Team lunch with client | €85.00 | Meals |
| Figma Annual License | €180.00 | Software |

**Thomas** — the Design team's manager, and in the `manager` group — would see all Design expenses:

| Description | Amount | Category | Employee |
|---|---|---|---|
| Flight to Munich | €450.00 | Travel | Anna Schmidt |
| Adobe Creative Suite | €720.00 | Software | Max Weber |
| Team lunch with client | €85.00 | Meals | Anna Schmidt |
| Figma Annual License | €180.00 | Software | Anna Schmidt |
| Train to Berlin | €120.00 | Travel | Max Weber |

Anyone in the `cfo` group sees everything across all teams — which is what you
just set up for yourself.


## Checkpoint

At this point you have:
- Model-level validation rules that block bad data
- API-level serializer validation from Part 2
- Role-based permissions (employee, manager, CFO)
- Your model permissions synced to [Keycloak](https://www.keycloak.org/documentation) with **Init**
- Your own login in the `cfo` group, so you can see every expense

Next up: [[start-here/tutorial/Part 5 — Streamlit Dashboards|Part 5 — Streamlit Dashboards]].
