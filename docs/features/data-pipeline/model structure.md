---
title: Model Structure
---

By default, all your models appear in the frontend sidebar under a generic "Models" group. To organize them into meaningful groups — and to control display names and history tracking — create a `model_structure.yaml` file in your project root.

## The Three Sections

The file has three top-level keys, all optional:

```yaml title="model_structure.yaml"
# ┌─────────────────────────────────────────────
# │ 1. model_structure — sidebar navigation tree
# │    Keys are group names; values are either
# │    model names (lowercase, value: null) or
# │    nested sub-groups (value: another dict).
# └─────────────────────────────────────────────
model_structure:
  Fund Management:
    fund: null
    quarter: null
    Investments:                # ← nested sub-group
      investment: null
      investmentrelationship: null
  Reporting:
    calculatenav: null
    calculatebalancesheet: null
  Uploads:
    uploadbalancesheet: null
    uploadinvestmentrelationships: null

# ┌─────────────────────────────────────────────
# │ 2. model_styling — display names for groups
# │    Adds emoji or custom labels to sidebar
# │    groups. Keys must match group names above.
# │    The only supported key is "name".
# └─────────────────────────────────────────────
model_styling:
  Fund Management:
    name: "🏦 Fund Management"
  Reporting:
    name: "📊 Reporting"
  Uploads:
    name: "📥 Uploads"

# ┌─────────────────────────────────────────────
# │ 3. untracked_models — skip history tracking
# │    Models listed here won't generate
# │    django-simple-history tables. They still
# │    appear in the sidebar if listed above.
# └─────────────────────────────────────────────
untracked_models:
  uploadbalancesheet: null
  uploadinvestmentrelationships: null
```

| Section | Purpose |
|---|---|
| `model_structure` | Defines the sidebar navigation tree. Group names are dict keys; model names (lowercase class name) are leaves with value `null`. Supports arbitrary nesting depth for sub-groups. |
| `model_styling` | Customizes group display names with emoji or labels. Keys must match the group names in `model_structure`. The only supported property is `name`. |
| `untracked_models` | Excludes models from [django-simple-history](https://django-simple-history.readthedocs.io/) tracking — no `Historical*` or `MetaHistorical*` tables are created. Useful for transient data like uploads to save storage. |

> [!tip]
> `model_structure.yaml` is optional. Without it, all models appear under a default "Models" group. Add it when your project grows beyond a handful of models.

## Format Rules

- **Model names must be lowercase** — they match the lowercased Python class name (e.g., `CalculateNAV` → `calculatenav`)
- **Leaf nodes** (models) always have value `null`
- **Non-leaf nodes** (groups) are dicts containing more nodes
- **Nesting is unlimited** — you can nest sub-groups inside groups, sub-sub-groups inside sub-groups, etc.
- **Models not listed** in `model_structure` are automatically placed under a catch-all "Models" group — you don't have to list every model

## Nested Sub-Groups

For large projects, use nested groups to keep the sidebar manageable. Here's a production example with reports organized by category:

```yaml title="model_structure.yaml"
model_structure:
  AdditionalReports:
    NAV:
      navoverview: null
      calculatenav: null
      investornav: null
    CapitalAccounts:
      capitalaccountreport: null
      capaccountdeltareport: null
    InvestorReporting:
      investorreportinput: null
      investorreport: null
```

Each level becomes a collapsible folder in the sidebar.

## Group Styling

The `model_styling` section controls how group names appear in the sidebar. The only supported property is `name`:

```yaml
model_styling:
  Teams & People:
    name: "👥 Teams & People"
  Expenses:
    name: "💶 Expenses"
  Reports:
    name: "📊 Reports"
```

Without styling, the raw group key from `model_structure` is used as the display name. Model leaves use the model's `verbose_name` by default.

## Untracked Models

Models listed under `untracked_models` are excluded from [django-simple-history](https://django-simple-history.readthedocs.io/) registration. This means:

- No `Historical*` table is created for the model
- No bitemporal history is recorded for changes
- The [[interface/record-detail/history tab|History tab]] and [[interface/record-detail/timeline tab|Timeline tab]] won't have data for these models

> [!important]
> Untracked models **still appear in the sidebar** if listed under `model_structure`. This setting only controls history tracking, not visibility.

We recommend untracking upload models and other transient data to save database storage:

```yaml
untracked_models:
  teamupload: null
  employeeupload: null
  expenseupload: null
```

The framework also automatically untracks internal models like `calculationlog`, `auditlog`, and `auditlogstatus`.

## Built-In Groups

Lex App automatically adds these groups to the sidebar — you don't need to define them:

| Group | Contents |
|---|---|
| **AuditLog** | `auditlog`, `auditlogstatus` |
| **Calculation Log** | `calculationlog` |
| **Streamlit** | `streamlit` (if `IS_STREAMLIT_ENABLED=true`) |

If you define these groups yourself in `model_structure`, your definition takes precedence.
