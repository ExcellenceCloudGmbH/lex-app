# Testing and Test Data

Search keywords: testing, tests, ProcessAdminTestCase, test_path, test_data.json, scenario, JSON test data, lex pytest, lex_test_config.yaml, test groups, pytestmark, minimal test data, is_calculated IN_PROGRESS

## Scope

- How a Lex App project's tests are written and run
- The JSON scenario-data paradigm and how it differs from Python fixtures
- `ProcessAdminTestCase` semantics: `test_path`, `setUp`, tag resolution
- Test file and scenario layout under `Tests/`
- The `lex pytest` runner and `lex_test_config.yaml` group selection
- The minimal-test-data rule

> This topic covers testing **a Lex App you built with the framework**. It does
> not cover testing the `lex-app` framework itself, which has its own cluster
> test plan.

## Key Points

- A Lex test scenario is defined in **JSON**, not Python. The JSON is a list of
  `create` / `update` / `delete` actions replayed into the database before the
  assertions run.
- Test classes subclass `ProcessAdminTestCase` from
  `lex.lex_app.tests.ProcessAdminTestCase`. Its `setUp` loads the scenario.
- The scenario JSON uses **the same action schema as initial data upload** — see
  `16-initial-data-upload.md`. Learn one, you know both.
- `test_path` on the test class points at the scenario's `test_data.json` and is
  resolved **relative to `PROJECT_ROOT`**.
- Objects are saved for real: hooks fire, validation runs, calculations execute.
  These are integration tests against a real database, not unit tests over mocks.
- A `CalculationModel` row must carry `"is_calculated": "IN_PROGRESS"` in the
  fixture or its calculation never runs.
- Tests run through `lex pytest`, which bootstraps Django and stands up the test
  database. Not bare `pytest`, not `manage.py test`.
- `lex_test_config.yaml` declares the tests entrypoint and the marker groups that
  `lex pytest -m <group>` selects.
- **Seed the minimum data that makes the assertion provable.** Surplus rows are
  maintenance cost with no coverage value.

## The Test Class

```python
from lex.lex_app.tests.ProcessAdminTestCase import ProcessAdminTestCase

from Input.Team import Team
from Input.Employee import Employee
from Input.Expense import Expense


class TestS01BudgetWithinLimit(ProcessAdminTestCase):
    """Scenario S01 — an expense under the approver's limit is approved.

    Covers: AC-001, AC-003.
    Run: lex pytest Tests/test_s01_within_limit.py
    """

    test_path = "Tests/scenarios/s01_within_limit/test_data.json"

    def test_01_01_expense_is_approved(self):
        """Scenario S01.1: expense under the limit is approved.

        Given: an approver with a 2000 limit and a 1500 expense
        When:  the budget calculation runs
        Then:  the expense status is 'approved'
        """
        expense = Expense.objects.get(reference="EXP-001")
        self.assertEqual(
            expense.status,
            "approved",
            f"AC-001: a 1500 expense under a 2000 limit must be approved, "
            f"got {expense.status!r}",
        )
```

### `test_path`

| Value | Behaviour |
|---|---|
| Set to a path | Resolved as `PROJECT_ROOT / test_path`. Use forward slashes; the framework normalises separators. |
| Left as `None` | The framework looks for `test_data.json` **next to the test module**. |

Prefer the explicit path — it keeps scenarios in their own directories and makes
the fixture a test module references obvious from the class body.

### What `setUp` does

`ProcessAdminTestCase.setUp` runs before **every** test method and:

1. Builds the registered-model map for the project app.
2. Reads the scenario JSON at `test_path`, recursively flattening every
   `subprocess` reference into one ordered action list.
3. Executes each action in order — resolving `tag:` and `datetime:` values, then
   calling `save()` (or `filter().delete()`) for real.
4. Records each created object under its `tag` in `self.tagged_objects`.

Because it re-runs per method, methods are independent and must not depend on
each other having run. Read objects back through the ORM rather than relying on
`self.tagged_objects` for assertions.

`setUpCloudStorage(generic_app_models, audit_logger=None)` is the variant that
also handles `FileField` uploads into the configured storage backend.

## Scenario Data

The action schema is identical to initial-data upload — see
`16-initial-data-upload.md` for the full reference. The essentials:

```json
[
  { "subprocess": "Tests/data/01_create_teams.json" },
  {
    "class": "Employee",
    "action": "create",
    "tag": "emp_anna",
    "parameters": {
      "first_name": "Anna",
      "team": "tag:team_design",
      "limit": 2000.0,
      "start_date": "datetime:2026-01-15"
    }
  }
]
```

| Field | Required for | Meaning |
|---|---|---|
| `class` | all | Registered model class name, matched exactly |
| `action` | all | `create`, `update`, or `delete` — no other verbs exist |
| `tag` | `create` | Label later actions reference via `tag:<name>` |
| `parameters` | `create`, `update` | Field name → value |
| `filter_parameters` | `update`, `delete` | Django field-lookup dict locating the target |

Two value prefixes exist and only two:

- `"tag:emp_anna"` — the object created earlier under that tag. Use for
  `ForeignKey` / `OneToOneField`.
- `"datetime:2026-01-15"` — parsed via `dateutil.parser`.

A `FileField` value is a **project-root-relative path** to a real file on disk.

### Rules that bite

1. **`subprocess` paths resolve against `PROJECT_ROOT`, not against the file
   containing them.** Write them project-root-relative
   (`"Tests/data/01_create_teams.json"`), or the load breaks on another machine.
2. **Order matters.** The flattened action list executes in order; a `tag:`
   producer must appear before every consumer. Parents before children.
3. **Every non-nullable field without a default must be supplied**, or `save()`
   raises and the whole scenario load fails.
4. **`CalculationModel` rows need `"is_calculated": "IN_PROGRESS"`.** Without it
   the calculation is never triggered, derived fields stay empty, and assertions
   on computed values grade a blank. This is the most common reason a Lex suite
   appears to pass while covering nothing.
5. **`CalculatedModelMixin` with defining fields expands combinatorially**, so
   the rows in the database can exceed the `create` actions in the file.

## Recommended Layout

```
Tests/
├── data/                                    ← shared action files
│   ├── 01_create_teams.json
│   └── 02_create_employees.json
├── scenarios/
│   ├── s01_within_limit/test_data.json      ← what test_path points at
│   └── s02_exceeded/test_data.json
├── UploadFiles/
│   └── Expenses/january.xlsx                ← FileField assets
├── test_s01_within_limit.py
├── test_s02_exceeded.py
└── __init__.py
```

Each scenario's `test_data.json` is a subprocess list pulling in whatever shared
data it needs plus its own scenario-specific rows. Share a `Tests/data/` file
only when the data is genuinely common — coupling two scenarios to one file so
they can share four rows makes both harder to change.

A simpler flat layout also works and is what the TeamBudget tutorial uses:
`Tests/test_data.json` as the top-level subprocess list with numbered action
files beside it, and `test_path = None` so the test module picks up
`test_data.json` from its own directory.

## Running Tests

```bash
lex pytest                                   # whole suite via tests_entrypoint
lex pytest Tests                             # explicit target
lex pytest -m budgets                        # one marker group
lex pytest -m "budgets or expenses"          # union
lex pytest -m "not stress"                   # exclusion
lex pytest Tests/test_s01_within_limit.py -v # one module
lex pytest --report                          # suite plus PDF/HTML report
lex pytest-groups                            # list groups without running
```

Run from the **project root** so `lex_test_config.yaml` and the tests entrypoint
are discovered.

Use `lex pytest`. It sets up the Django test environment and creates the test
database the way `manage.py test` would; bare `pytest` errors in `setUp` on every
`TestCase`-derived test, and `manage.py test` is not the supported path.

## `lex_test_config.yaml`

Lives at the project root and is the single source of truth for test-group
selection and reporting.

```yaml
tests_entrypoint: Tests

receivers: []

report:
  output_dir: test-runs/reports

email:
  from_email: "noreply@example.com"
  from_name: "Lex Reports"
  reply_to: "noreply@example.com"
  subject_prefix: "Lex test report"

groups:
  - { name: budgets,  description: "Budget approval scenarios." }
  - { name: expenses, description: "Expense ingestion and validation." }
```

| Key | Meaning |
|---|---|
| `tests_entrypoint` | Path pytest targets when no explicit target is given |
| `groups` | Marker groups; each must be used by at least one module |
| `report.output_dir` | Where `lex pytest --report` writes |
| `receivers`, `email` | Report delivery configuration |

Each test module opts into a group with a module-level marker:

```python
import pytest

pytestmark = pytest.mark.budgets
```

Every marker used must be declared in `groups`, and every declared group should
be used by something — an undeclared marker fails validation, and an unused group
misleads the next reader.

## The Minimal-Test-Data Rule

Test data exists to make an assertion provable. Every row is one of two things:

- **assertion row** — an assertion reads it, or reads a value computed from it,
- **structural row** — it exists only to satisfy a non-nullable FK on an
  assertion row.

There is no third category. A row that is neither is surplus: it has to be kept
correct as the schema moves, it makes the scenario harder to read, and it proves
nothing.

The counter-rule matters too: a scenario testing an aggregate or a filter needs
enough rows to *discriminate*. One row that must be included and one that must be
excluded — otherwise the assertion passes under a wrong filter. And prefer input
values where the correct answer is unique: expenses of 100 + 250 catch formula
errors that 100 + 100 does not.

## Common Pitfalls

| Pitfall | Consequence |
|---|---|
| Missing `"is_calculated": "IN_PROGRESS"` | Calculation never runs; value assertions grade a default |
| `test_path` that does not resolve | `setUp` loads nothing; count assertions can still pass against an empty DB |
| `subprocess` path relative to its own file | Load fails wherever `PROJECT_ROOT` differs |
| `tag:` consumer before its producer | Unresolved reference at load time |
| Asserting only row counts | Proves the fixture loaded, not that the application works |
| Deriving expected values by running the code | Pins current behaviour including bugs; the test can never fail |
| Weakening an assertion to reach green | Converts a real defect into documented wrong behaviour |
| Mocking the ORM | Defeats the point — these are integration tests against a real database |

Mock only genuine external boundaries: Keycloak HTTP, the Celery broker, channel
layers, S3, SharePoint, outbound email.

## Where to Expand

- `16-initial-data-upload.md` — the full action schema and prefix reference
- `04-calculationmodel-lifecycle.md` — status transitions, `is_atomic`, dispatch
- `05-calculatedmodelmixin-combinatorics.md` — defining fields and expansion
- `17-cli-settings-imports-utils.md` — the wider `lex` CLI surface

## LLM Prompt Starters

- "Write a `ProcessAdminTestCase` for this scenario, with `test_path` pointing at
  its `test_data.json` and assertions derived from these acceptance criteria."
- "Create the minimal JSON scenario data for this test: which rows are assertion
  rows, which are structural, and nothing else."
- "Set up `lex_test_config.yaml` groups for these test modules and add the
  matching `pytestmark` lines."
- "Check whether this test would actually fail if the behaviour it covers broke."
