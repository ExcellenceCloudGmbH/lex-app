# Process Admin & Model Structure

Search keywords: ProcessAdminSite, model registration, model_structure.yaml, ModelContainer, model_styling, untracked_models

## Scope

- How models become available in API/UI
- `model_structure.yaml` for navigation and grouping
- Model styling and untracked models

## Key Points

- Model registration is handled via process admin/site mechanisms.
- Navigation and grouping are driven by `model_structure.yaml` (or Python fallback).
- Model containers carry metadata for dynamic endpoint generation.

## model_structure.yaml

Optional file with three sections:

```yaml
# 1. model_structure — sidebar navigation tree
model_structure:
  Fund Management:
    fund: null
    quarter: null
    Investments:                # nested sub-group
      investment: null
      investmentrelationship: null
  Reporting:
    calculatenav: null
  Uploads:
    uploadbalancesheet: null

# 2. model_styling — display names for groups
model_styling:
  Fund Management:
    name: "🏦 Fund Management"
  Reporting:
    name: "📊 Reporting"
  Uploads:
    name: "📥 Uploads"

# 3. untracked_models — skip history tracking
untracked_models:
  uploadbalancesheet: null
```

### Section Reference

| Section | Purpose |
|---|---|
| `model_structure` | Sidebar navigation tree. Group names = dict keys; model names (lowercase class name) = leaves with value `null`. Supports nesting. |
| `model_styling` | Custom group display names with emoji. Keys must match `model_structure`. Only `name` property supported. |
| `untracked_models` | Excludes models from django-simple-history. No `Historical*` table created. |

### Format Rules

- **Model names must be lowercase** — match lowercased Python class name (`CalculateNAV` → `calculatenav`)
- **Leaf nodes** (models) always have value `null`
- **Non-leaf nodes** (groups) are dicts containing more nodes
- **Nesting is unlimited**
- **Models not listed** go under a catch-all "Models" group

### Built-In Groups (auto-added)

| Group | Contents |
|---|---|
| **AuditLog** | `auditlog`, `auditlogstatus` |
| **Calculation Log** | `calculationlog` |

Your definitions take precedence if you define these yourself.

## Where to Expand

- `lex_context.md`: Model Registration & model_structure.yaml
- `lex_context_repo.md`: Process Admin — Model Registration & Structure

## LLM Prompt Starters

- "Generate a `model_structure.yaml` for these models with clean sidebar grouping."
- "Explain why this model is missing in API/UI by checking registration and structure mapping paths."
