# Testing and Test Data

Search keywords: testing, tests, test_data.json, JSON test format, subprocess, action file, ProcessAdminTestCase, test_path, INITIAL_DATA, initial data, lex pytest, lex start, tag prefix, datetime prefix, FileField upload, is_calculated IN_PROGRESS, reset-first, keepdb, no isolation, ground truth, GroundTruth, upload_test, stub test

## Scope

How a Lex App project's tests are written, laid out and run — from the
perspective of someone building a Lex app, not someone maintaining the
framework.

> This topic covers testing **a Lex App you built with the framework**. It does
> not cover testing the `lex-app` framework itself, which has its own cluster
> test plan and is ordinary pytest.

---

## 1. The one thing to understand first

**In a Lex app, the test is a JSON file.**

Not the test *data* — the test itself. A Lex test is an ordered list of
`create` / `update` / `delete` actions against your registered models, written
in a specific JSON structure, which the framework replays into a real database.
Every object saves for real: lifecycle hooks fire, validation runs, parsers
parse the uploaded workbooks, calculations execute, cascades cascade.

The pass condition of the most common Lex test is simply: **the whole chain
loaded without raising.** If a parser chokes on a column, if a required field is
missing, if a calculation throws, if a FK does not resolve — the load raises and
the test fails.

This is why most real Lex projects have `Tests/` folders containing **only JSON
and spreadsheets, with no Python test file at all**, and why the ones that do
have a Python file often look like this in full:

```python title="DiF/Tests/upload_test.py"
from lex.lex_app.tests.ProcessAdminTestCase import ProcessAdminTestCase


class upload_test(ProcessAdminTestCase):

    def test(self):
        pass
```

That empty `test()` body is not a placeholder anyone forgot to finish. The work
happens in `setUp`, which loads the JSON. The method exists only to give the
runner something to collect.

Python assertions are an **optional third layer** on top (§8). Reaching for them
first — writing a pytest module per behaviour and a small fixture to feed it — is
the single most common way of getting Lex testing wrong.

---

## 2. The two ways a test JSON runs

The same file format, the same loader class, two entry points:

| | Initial-data path | Test path |
|---|---|---|
| Triggered by | `lex start` (server boot) | `lex pytest` |
| Configured by | `INITIAL_DATA` in `lex_config.py` | `test_path` on a `ProcessAdminTestCase` subclass |
| Loader method | `ProcessAdminTestCase.setUpCloudStorage()` | `ProcessAdminTestCase.setUp()` |
| Runs when | **only if every referenced model is empty** | every time, per test method |
| `FileField` handling | file is read from disk and copied into `default_storage` | **the raw path string is assigned as-is** (see §6.3) |
| Failure looks like | server logs an error at boot | test error |

The framework literally instantiates the test case to do the seeding — in
`lex/lex_app/apps.py`:

```python
from lex.lex_app.tests.ProcessAdminTestCase import ProcessAdminTestCase
test = ProcessAdminTestCase()
...
test.test_path = project_config.initial_data
```

One mechanism, two jobs. That is why the seed folder is called `Tests/` and the
seed file is called `test_data.json` even when nobody is running tests, and why
many projects point `INITIAL_DATA` straight at their test chain:

```python title="lex_config.py"
INITIAL_DATA = "Tests/test_data.json"
```

The auto-load is **all-or-nothing**: if even one referenced model already has
rows, the entire load is skipped silently. To re-trigger it, recreate the
database.

---

## 3. The file format

### 3.1 The root file — a switchboard

The file `test_path` / `INITIAL_DATA` points at is normally not the data. It is
an ordered list of `subprocess` references naming the files that are:

```json title="Tests/test_data.json"
[
  {"subprocess": "Tests/01_create_teams.json"},
  {"subprocess": "Tests/02_create_employees.json"},
  {"subprocess": "Tests/03_create_expenses.json"}
]
```

Subprocess files may themselves contain `subprocess` entries; the framework
flattens the whole tree recursively into **one ordered action list** and executes
it top to bottom.

Because the root file is just a switchboard, selecting which case runs is
normally done by **editing it**. This is the real-world pattern from a
production project, where a `delete` clears the world and a single subprocess
selects the month under test:

```json title="DiF/Tests/test_data.json"
[
  {
    "class": "Upload",
    "action": "delete",
    "filter_parameters": {}
  },
  {
    "subprocess": "DiF/Tests/2025_06/EU_APAC/test_data_2025_06.json"
  }
]
```

Swap the subprocess line to run a different period. Comment-free JSON means
alternatives live as sibling files, not as commented-out lines.

### 3.2 Action files — the actual test

Every non-`subprocess` entry is an action object:

```json title="Tests/01_create_teams.json"
[
  {
    "class": "Team",
    "action": "create",
    "tag": "team_design",
    "parameters": {
      "name": "Design",
      "budget": 15000.00
    }
  }
]
```

| Field | Required for | Meaning |
|---|---|---|
| `class` | all | Registered model class name, matched **exactly** |
| `action` | all | `create`, `update` or `delete` — **no other verbs exist** |
| `tag` | `create` | Label later actions reference via `tag:<name>`. Defaults to `"instance"` if omitted |
| `parameters` | `create`, `update` | Field name → value |
| `filter_parameters` | `update`, `delete` | Django field-lookup dict locating the target |
| `subprocess` | — | Instead of all the above: path to another JSON file |

### 3.3 The three actions

**`create`** — instantiates `Model(**parameters)` and calls `save()`. Hooks,
validation and calculations all run.

**`update`** — `Model.objects.filter(**filter_parameters).first()`, then sets each
key in `parameters`, then `save()`. Note **`.first()`**: an `update` touches one
row, the first match. If nothing matches, the action is a silent no-op.

```json
{
  "class": "Employee",
  "action": "update",
  "tag": "emp_anna_promoted",
  "filter_parameters": { "email": "anna@example.com" },
  "parameters": { "role": "manager" }
}
```

**`delete`** — `Model.objects.filter(**filter_parameters)` and deletes every
match. `"filter_parameters": {}` deletes **all** rows of that class.

```json
{ "class": "Upload", "action": "delete", "filter_parameters": {} }
```

### 3.4 The two value prefixes

Exactly two exist. Inventing a third produces a string where a value was meant,
and the failure surfaces far away.

**`tag:`** — resolves to an object created earlier **in the same load**. Use for
`ForeignKey` / `OneToOneField`:

```json
"team": "tag:team_design"
```

**`datetime:`** — parsed with `dateutil.parser`, so most formats work
(`2026-01-15`, `2026-01-15T09:30:00`, `15/01/2026`):

```json
"start_date": "datetime:2026-01-15"
```

Tags live in a dict that is **rebuilt on every load**. A `tag:` consumer must
appear after its producer in the *flattened* order — which means across
subprocess boundaries too, in subprocess order.

---

## 4. The three levels of Lex test

Pick the lowest level that answers the question. Most projects never leave
level 1.

### Level 1 — JSON only (the default)

`Tests/` holds `test_data.json`, its action files and its workbooks. No Python
at all. Wire it through `INITIAL_DATA` and the check is: does the app boot with
this data?

```
Tests/
├── test_data.json
├── 00_reset.json
├── 01_create_funds.json
├── 02_create_companies.json
└── UploadFiles/
    └── demo_funds.csv
```

This proves a great deal: every parser handled its real file, every required
field was satisfiable, every FK resolved, every calculation ran to completion.
For an app whose job is "ingest these spreadsheets and compute", that is most of
the risk.

### Level 2 — JSON plus a stub class

Add a near-empty `ProcessAdminTestCase` so `lex pytest` can collect and run the
chain on demand rather than only at boot:

```python title="Tests/upload_test.py"
from lex.lex_app.tests.ProcessAdminTestCase import ProcessAdminTestCase


class upload_test(ProcessAdminTestCase):
    # test_path omitted → test_data.json is read from THIS directory
    def test(self):
        pass
```

Same pass condition as level 1, but now it runs in CI and reports as a test.

### Level 3 — JSON plus real assertions

Only when there is an answer worth checking — a computed number, a generated
report — add assertion methods. See §8.

---

## 5. Layout — where the files go

Two conventions are both in production use.

### 5.1 Top-level `Tests/` (most projects, and the tutorial)

```
Tests/
├── test_data.json               ← root switchboard
├── 00_reset.json                ← deletes, reverse-dependency order
├── 01_create_teams.json
├── 02_create_employees.json
├── UploadFiles/                 ← real workbooks for FileField parameters
│   └── Expenses/january.xlsx
├── GroundTruth/                 ← optional, level 3 only
│   └── gt_BudgetReport.xlsx
├── upload_test.py               ← optional stub or assertion module
└── __init__.py
```

### 5.2 Per-module `<Module>/Tests/` (large multi-module apps)

A big app splits its tests next to the module they exercise, and organises cases
by the dimension that actually varies — usually period and region:

```
DiF/Tests/
├── test_data.json                       ← switchboard: delete + one subprocess
├── upload_test.py
├── __init__.py
├── 2025_06/
│   └── EU_APAC/
│       ├── test_data_2025_06.json       ← one case
│       ├── Liabilities_by_Award_NQDC06_20250522.xlsx
│       ├── HR_Master_Data_06_25_EU.APAC.xlsx
│       └── SoSec.xlsx
├── 2025_01/
│   ├── EU_APAC/…
│   └── US/…
└── 2024_12/
    ├── EU_APAC/…
    └── US/…
```

Note what this layout does: **the workbooks live in the same folder as the JSON
that references them.** A case is one self-contained directory — the JSON plus
the exact files that reproduce that month. Adding next month means copying a
folder, dropping in the new workbooks and editing the switchboard.

A case file is often a single action:

```json title="DiF/Tests/2025_06/EU_APAC/test_data_2025_06.json"
[
  {
    "class": "Upload",
    "action": "create",
    "tag": "jun_2025",
    "parameters": {
      "name": "06/2025",
      "date": "datetime:2025-06-30",
      "is_US": false,
      "ewm_upload": "DiF/Tests/2025_06/EU_APAC/Liabilities_by_Award_NQDC06_20250522.xlsx",
      "hr_master_data": "DiF/Tests/2025_06/EU_APAC/HR_Master_Data_06_25_EU.APAC.xlsx",
      "is_calculated": "IN_PROGRESS"
    }
  }
]
```

One `create`. Behind it: three spreadsheets parsed, thousands of rows written,
a full liability calculation run. **That is the test.**

---

## 6. Path resolution — the part that bites

Three different rules apply to three different paths. Getting these wrong is the
most common reason a chain "loads nothing" or breaks on another machine.

### 6.1 `test_path` on the test class

| Value | Resolved as |
|---|---|
| omitted / `None` | `test_data.json` **in the same directory as the test module** |
| set | `$PROJECT_ROOT/<test_path>` |

```python
test_path = "Tests/scenarios/s01_within_limit/test_data.json"   # forward slashes
```

`PROJECT_ROOT` is the directory containing `.env`; the `lex` CLI sets it and
`lex pytest` also `chdir`s there.

### 6.2 `subprocess` paths — always PROJECT_ROOT-relative

**A `subprocess` path is resolved against `$PROJECT_ROOT`, never against the file
containing it — even when the root file was found next to the test module.**

So a chain at `DiF/Tests/test_data.json` must write:

```json
{"subprocess": "DiF/Tests/2025_06/EU_APAC/test_data_2025_06.json"}
```

and not `"2025_06/EU_APAC/test_data_2025_06.json"`. Write every subprocess path
from the project root down, with forward slashes.

### 6.3 `FileField` paths — and the trap

A `FileField` value is a path string. What happens next **differs between the two
execution paths**:

- **Initial-data load (`setUpCloudStorage`)** opens the file at
  `os.getcwd()/<path>` and copies it into `default_storage`, honouring the
  field's `upload_to`.
- **Test load (`setUp`)** does **not** do any of that. It assigns the raw string
  to the field, so Django treats it as a storage-relative name.

Practical consequence: keep `FileField` paths project-root-relative and run from
the project root — `lex pytest` chdirs there for you. If a workbook loads at
server boot but not under test, this asymmetry is why.

---

## 7. No isolation — and the reset-first rule

`ProcessAdminTestCase` extends `unittest.TestCase`, **not**
`django.test.TestCase`, so there is no per-test transaction. And `lex pytest`
stands the database up with `keepdb=True`, so it is not recreated between runs
either.

| Boundary | Rolled back? |
|---|---|
| Between test methods | **No** — `setUp` re-runs and creates the rows *again*, on top of the last run's |
| Between test classes | **No** — earlier chains' rows are still there |
| Between `lex pytest` runs | **No** — `keepdb=True`; yesterday's rows are still there |

Two rules follow. They are not stylistic.

### 7.1 Open every chain with a reset

```json title="Tests/00_reset.json"
[
  { "class": "Expense",  "action": "delete", "filter_parameters": {} },
  { "class": "Employee", "action": "delete", "filter_parameters": {} },
  { "class": "Team",     "action": "delete", "filter_parameters": {} }
]
```

Reverse-dependency order: children before parents. Every class the chain later
creates must appear. A chain without a reset is **not repeatable** — counts
drift on the second run and unique constraints fire.

(The DiF switchboard in §3.1 does this inline with a single `Upload` delete,
because deleting the upload cascades to everything it produced. Either shape is
fine; what matters is that something clears the world first.)

### 7.2 Guard the load on multi-method classes

Because `setUp` re-runs per method, a class with several test methods reloads its
whole dataset per method. Every real project uses the class-flag guard:

```python
class BasicTest(ProcessAdminTestCase):
    test_path = "Tests/basic_test/test_data.json"

    data_loaded = False

    def setUp(self):
        if not BasicTest.data_loaded:
            super().setUp()
            BasicTest.data_loaded = True
```

Reference the flag on the **concrete class**. `self.data_loaded` reads the
base-class attribute, so the guard never latches.

---

## 8. Calculations, and asserting on results

### 8.1 Making a calculation actually run

A `CalculationModel` row must carry `"is_calculated": "IN_PROGRESS"` in its
parameters, or `calculate()` never fires, derived fields stay empty, and any
assertion on a computed value grades a default:

```json
{
  "class": "BudgetReport",
  "action": "create",
  "tag": "report_q1",
  "parameters": {
    "quarter": "tag:q1_2026",
    "is_calculated": "IN_PROGRESS"
  }
}
```

This is the most common reason a Lex suite appears to pass while covering
nothing.

> **Legacy note.** V1 projects used `"calculate": true`, backed by a
> `CalculateField` on the old `generic_app` upload model. That field no longer
> exists — `CalculationModel` supersedes it. If you are reading an old test
> chain, `"calculate": true` is the V1 spelling of
> `"is_calculated": "IN_PROGRESS"`. Likewise
> `from generic_app.tests.ProcessAdminTestCase import ...` is the V1 import; the
> current one is `from lex.lex_app.tests.ProcessAdminTestCase import ...`.

### 8.2 Ground truth — where expected numbers come from

For anything numeric, the expected values must come from **outside the
application**, or the assertion is circular: a number read off a previous run can
only fail if the app becomes non-deterministic.

The convention is a workbook under `Tests/GroundTruth/`, supplied by whoever owns
the business logic. The test reads both sides and compares:

```python
def compare_report(self, sheet_name, primary_keys, columns, decimals=4):
    expected = pd.read_excel(GT_PATH, sheet_name=sheet_name)
    report = BudgetReport.objects.get(quarter__name="24 Q1")
    actual = pd.read_excel(report.report, sheet_name=sheet_name)

    merged = pd.merge(expected, actual, on=primary_keys, how="outer",
                      suffixes=("_gt", "_actual"), indicator=True)

    unmatched = merged[merged["_merge"] != "both"]
    if not unmatched.empty:
        self.fail(f"{sheet_name}: row sets differ\n{unmatched}")

    for column in columns:
        gt, act = f"{column}_gt", f"{column}_actual"
        if not (merged[gt].round(decimals) == merged[act].round(decimals)).all():
            diff = merged[merged[gt].round(decimals) != merged[act].round(decimals)]
            self.fail(f"{sheet_name}.{column} differs\n{diff[[*primary_keys, gt, act]]}")
```

Two halves, both required: an **outer** merge to catch missing and extra rows,
then per-column comparison at a declared precision. Checking values without
checking the row set hides whole missing groups; exact float equality on derived
financials fails for reasons unrelated to correctness.

If no ground truth exists, **ask for it**. Do not infer expected numbers from the
code, and do not generate a "ground truth" file by running the app.

### 8.3 What not to assert

```python
# The chain creates exactly 2 EmployeeStatus rows.
self.assertEqual(EmployeeStatus.objects.count(), 2)
```

This tests `json.load`. It would pass with every calculation in the project
deleted. Assert what the application **derived**, never what the chain
**stated** — and remember that at level 1 the chain loading at all is already
the assertion.

---

## 9. Running tests

```bash
lex pytest                                   # whole suite via tests_entrypoint
lex pytest Tests                             # explicit target
lex pytest -m budgets                        # one marker group
lex pytest -m "not stress"                   # exclusion
lex pytest DiF/Tests/upload_test.py -v       # one module
lex pytest --report                          # suite plus PDF/HTML report
lex pytest-groups                            # list groups without running
```

Run from the **project root**. Use `lex pytest`, not bare `pytest` — it
bootstraps Django and stands up the database; bare `pytest` errors in `setUp` on
every test. `manage.py test` is not a supported path.

For a level-1 (JSON-only) project the "run" is simply `lex start`, watching the
boot log for the load.

### `lex_test_config.yaml`

Optional, at the project root. It names the entrypoint and the marker groups
`lex pytest -m <group>` selects:

```yaml
tests_entrypoint: Tests

groups:
  - { name: budgets,  description: "Budget approval scenarios." }
  - { name: expenses, description: "Expense ingestion and validation." }
```

A module opts into a group with `pytestmark = pytest.mark.budgets`. Every marker
used must be declared, and every declared group should be used — an undeclared
marker fails validation. A project with a single stub test does not need this
file at all.

---

## 10. Common pitfalls

| Pitfall | Consequence |
|---|---|
| Writing a pytest module per behaviour with a small fixture behind it | The Python-first inversion. You end up testing the ORM and `json.load` instead of the pipeline |
| `subprocess` path relative to its own file | Resolves against `$PROJECT_ROOT`; load silently finds nothing or breaks on another machine |
| No reset at the head of the chain | Rows accumulate across methods, classes and runs; counts drift, unique constraints fire |
| Multi-method class without the `data_loaded` guard | Whole dataset re-created per method |
| `self.data_loaded` instead of `MyClass.data_loaded` | Reads the base attribute; guard never latches |
| Missing `"is_calculated": "IN_PROGRESS"` | Calculation never runs; value assertions grade a default |
| `"calculate": true` on a V2 project | V1 spelling; silently does nothing useful |
| `tag:` consumer before its producer in flattened order | Unresolved reference at load time |
| Creating the rows a parser would have produced | Skips parser, validation hooks and calculation trigger entirely |
| `update` expecting to touch many rows | It calls `.first()` — one row only |
| Non-matching `update` / `delete` filter | Silent no-op; the chain "passes" having done nothing |
| Asserting a count the chain literally sets | Tautology; cannot fail for any application reason |
| Deriving expected values by running the code | Pins current behaviour including bugs |
| Mocking the ORM | Defeats the point — these are integration tests against a real database |

Mock only genuine external boundaries: Keycloak HTTP, the Celery broker, channel
layers, S3, SharePoint, outbound email.

---

## 11. Writing a test for a new Lex app — the practical sequence

1. Create `Tests/` with an `__init__.py`.
2. Drop the **real input files** the app is meant to consume into
   `Tests/UploadFiles/` (or into the per-case folder, §5.2). Real files, not
   invented ones — the parser is the thing under test.
3. Write `Tests/00_reset.json` deleting every class you will create, children
   first.
4. Write numbered action files for the chain: context/reference rows first, then
   the upload row(s) that drive the pipeline. Set
   `"is_calculated": "IN_PROGRESS"` on every `CalculationModel` row.
5. Write `Tests/test_data.json` chaining the reset and then the action files in
   order.
6. Point `INITIAL_DATA` at it in `lex_config.py`, run `lex start`, and fix what
   the load surfaces. This is the bulk of the work and most of the value.
7. *If you want it in CI:* add the stub `ProcessAdminTestCase` (§4 level 2).
8. *If there are numbers worth checking:* obtain a ground-truth workbook and add
   assertion methods (§8.2), with the `data_loaded` guard.

---

## 12. Where to expand

- `16-initial-data-upload.md` — the same action schema from the seeding side
- `04-calculationmodel-lifecycle.md` — status transitions, `is_atomic`, dispatch
- `05-calculatedmodelmixin-combinatorics.md` — defining fields and expansion
- `17-cli-settings-imports-utils.md` — the wider `lex` CLI surface

## LLM Prompt Starters

- "Write the `Tests/` chain for this app: reset file, action files driving the
  real uploads, and the `test_data.json` switchboard that chains them."
- "This chain fails on load — walk the flattened action order and find where the
  `tag:` reference or the required field is wrong."
- "Add a per-period case folder under `<Module>/Tests/` for this month's
  workbooks and wire it into the switchboard."
- "Compare this generated report against `Tests/GroundTruth/gt_*.xlsx`:
  outer-merge on the primary keys, then compare these columns at 4 decimals."
- "Check whether this test would actually fail if the behaviour it covers broke."
