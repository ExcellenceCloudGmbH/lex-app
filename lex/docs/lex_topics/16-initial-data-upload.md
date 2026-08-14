# Initial Data Upload

Search keywords: initial data, seed data, JSON, test data, lex_config, subprocess, tag, datetime, INITIAL_DATA

## Scope

- Seeding the database with structured JSON on server start
- JSON format: subprocesses, actions (create/update/delete), tag references
- Auto-load conditions and execution order

## Key Points

- Initial data upload lets you define seed data as JSON and have the framework load it automatically on server start.
- Configured via `INITIAL_DATA` in `lex_config.py` (path relative to project root).
- Auto-load is all-or-nothing: if even one referenced model has existing data, the entire load is skipped.
- Three actions: `create`, `update`, `delete`.
- Supports foreign key references via `tag:` prefix and date parsing via `datetime:` prefix.
- File uploads for `FileField` parameters use relative paths from the project root.
- For model classes that include a CalculationModel include `"is_calculated": "IN_PROGRESS"` in the json so the calculations are tested.
- **This is the same file format Lex tests use.** The loader is literally the same class (`ProcessAdminTestCase`), so a JSON chain written for seeding *is* a test and vice versa. See `23-testing-and-test-data.md` for the testing side: chain layout, the reset-first rule, and how a chain is run on demand rather than at boot.

## Configuration

```python
# lex_config.py
INITIAL_DATA = "Tests/test_data.json"
```

## JSON Format

### Top-Level File (Subprocess List)

```json
[
  {"subprocess": "Tests/01_create_teams.json"},
  {"subprocess": "Tests/02_create_employees.json"},
  {"subprocess": "Tests/03_create_expenses.json"}
]
```

Subprocesses are executed in order. Subprocesses can themselves contain `subprocess` references — the framework flattens them recursively.

### Action Files

```json
[
  {
    "class": "Team",
    "action": "create",
    "tag": "team_design",
    "parameters": {
      "name": "Design",
      "budget": 15000.00,
      "manager_email": "thomas.mueller@apex-consulting.com"
    }
  }
]
```

### Action Object Fields

| Field | Required | Description |
|---|---|---|
| `class` | ✅ | Model class name (must match a registered model) |
| `action` | ✅ | One of `create`, `update`, or `delete` |
| `tag` | For `create` | A label to reference this object later via `tag:` |
| `parameters` | For `create`/`update` | Field names → values to set |
| `filter_parameters` | For `update`/`delete` | Django queryset filter to find existing objects |

## Actions

### `create`
Creates a new model instance. The `tag` is stored in memory for later `tag:` references.

```json
{
  "class": "Employee",
  "action": "create",
  "tag": "emp_anna",
  "parameters": {
    "first_name": "Anna",
    "last_name": "Schmidt",
    "team": "tag:team_design"
  }
}
```

### `update`
Finds an existing object via `filter_parameters`, then sets the fields in `parameters`:

```json
{
  "class": "Employee",
  "action": "update",
  "tag": "emp_anna_updated",
  "filter_parameters": {
    "email": "anna.schmidt@apex-consulting.com"
  },
  "parameters": {
    "role": "manager"
  }
}
```

### `delete`
Deletes all objects matching `filter_parameters`. Empty `{}` deletes all instances.

## Special Value Prefixes

### `tag:` — Foreign Key References
Resolves to the actual model instance created earlier in the same data load:
```json
"team": "tag:team_design"
```

### `datetime:` — Date and Time Parsing
Parses date strings into Python datetime objects:
```json
"date": "datetime:2026-01-15"
```

## Auto-Load Conditions

All of the following must be true:

| Condition | Details |
|---|---|
| `INITIAL_DATA` is set | In `lex_config.py` or `_authentication_settings.py` |
| Path is valid | File exists and contains well-formed JSON |
| JSON structure correct | Top-level list of `subprocess` entries or action objects |
| All referenced models empty | Every model class in `class` fields has zero rows |
| Server starting normally | Via `lex start`, not during Init or Celery worker |

To re-trigger: `lex create_db` → `lex Init` → `lex start`.

## Execution Order

1. Top-level JSON is read, `subprocess` references recursively flattened into flat action list
2. Actions processed in order
3. `tag:` and `datetime:` values in `parameters`/`filter_parameters` resolved
4. Action (`create`/`update`/`delete`) executed
5. Order matters: parent `create` must appear before child referencing it via `tag:`

## Example Project Structure

```
Tests/
├── test_data.json                  ← Top-level subprocess list
├── 01_create_teams.json
├── 02_create_employees.json
├── 03_create_expenses.json
└── UploadFiles/
    └── Structure/
        ├── VehicleUpload.xlsx
        └── InvestorUpload.xlsx
```

## Where to Expand

- `23-testing-and-test-data.md` — the same format used as a test: layout, reset-first, running, assertions
- `lex_context.md`: Configuration Files; Testing & Initial Data

## LLM Prompt Starters

- "Create an initial data JSON setup for these models with proper tag references and execution order."
- "Set up `lex_config.py` and seed data files for test data loading."
