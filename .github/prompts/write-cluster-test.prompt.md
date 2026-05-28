---
description: "Scaffold a test-plan-aligned pytest file following the LEX cluster naming and allocation rules. Walks the dev through the test-plan lookup and confirms cluster/letter before writing."
mode: agent
---

# /write-cluster-test — Scaffold a cluster-aligned test

You are helping write a test that follows the LEX test-plan structure in `lex/test_project/test-plan/`. **Follow these steps strictly and do not improvise.** The test-plan rules in `.github/instructions/testing.instructions.md` apply throughout.

## Inputs to gather (one at a time, only if not already provided)

1. The **source module/file** the user wants to test (e.g., `lex/lex_app/fast_health.py`).
2. The **behaviour** under test (one-line description).
3. Optional: a **specific cluster** the user already has in mind. If not provided, you'll infer it in Step 2.

## Step 1 — Read the test-plan

Open and read **all** of these before doing anything else:

1. [`lex/test_project/test-plan/index.md`](../../lex/test_project/test-plan/index.md) — overview of all clusters.
2. [`lex/test_project/test-plan/test-clusters.md`](../../lex/test_project/test-plan/test-clusters.md) — cluster definitions and topic mapping.
3. [`lex/test_project/test-plan/test-writing-plan.md`](../../lex/test_project/test-plan/test-writing-plan.md) — current batch state, in-flight clusters, allocation rules.

If you cannot find any of these files, **stop and tell the user**. Do not guess.

## Step 2 — Identify the cluster

Map the source module to a cluster using `test-clusters.md`. Common mappings:

| Source area | Cluster |
| --- | --- |
| `lex/lex_app/__init__.py`, settings, urls, views, bootstrap | 1 |
| `lex/lex_app/LexModel.py`, ORM models | 3, 4 |
| Audit logging | 6 |
| Calculation models, calculation flow | 7 |
| Celery / async / workers | 8 |
| Serializers, REST | 12 |
| Model export / import | 13 |
| List views, AG Grid | 14 |

**If unsure, ask the user which cluster.** Do not pick one yourself when ambiguous.

## Step 3 — Allocate the next free letter and scenario range

From `test-writing-plan.md`:

1. List every batch already documented for the target cluster (e.g., 1a, 1b, ..., 1n, 1o, 1p).
2. The next free letter is the next alphabetical letter after the highest one in use. **Cluster letters are never renumbered.**
3. Find the cluster's current maximum scenario ID. The new batch starts at `max + 1`.
4. Check the "Pending decisions" and "in-flight Tier-A clusters" sections — if the source file is already slotted in an in-flight batch, **stop and tell the user**. Do not duplicate.

## Step 4 — Determine the test type letter

| Letter | Class | When |
| --- | --- | --- |
| **U** | `SimpleTestCase` or plain pytest function | Pure logic, no DB. **Prefer this.** |
| **I** | `TestCase` | ORM access, per-test transaction rollback |
| **E** | `APITestCase` + `APIClient` | REST endpoint testing |

Default to **U** unless the test clearly needs DB or HTTP plumbing.

## Step 5 — Pause and confirm

Before scaffolding, output exactly this format and **wait for the user's confirmation**:

```
Allocating new batch:
  Cluster:        <NN><letter>          (e.g., 7h)
  Scenarios:      <start> – <end>       (e.g., 7.123 – 7.140)
  Type:           U | I | E
  Test file:      lex/test_project/tests/<topic>/test_<NN><letter>_<short>.py
  Test classes:   TestCluster<NN><letter>_<Description>
  Files covered: <list of source files>
  Fixtures:       <list, or "none">

Confirm? (yes / change cluster / change type)
```

If the user says no or wants changes, revise and re-confirm. **Do not proceed without explicit confirmation.**

## Step 6 — Scaffold the test file

Create the file at the path from Step 5 with this exact header:

```python
"""<one-line summary of what these tests cover>.

Cluster <NN><letter> — scenarios <start>–<end>.
Type: <U | I | E>.

Covers: <files covered, comma-separated>.
Run: pytest lex/test_project/tests/<topic>/test_<NN><letter>_<short>.py -v
"""

from <appropriate imports>


class TestCluster<NN><letter>_<Description>(<SimpleTestCase | TestCase | APITestCase>):
    """<one-line class docstring describing the cluster of scenarios>."""

    def test_<behaviour_under_test>(self):
        """Scenario <NN><letter>.<id> — <one-line scenario description>."""
        # arrange
        ...
        # act
        ...
        # assert
        self.assertEqual(...)
```

Apply all rules from `.github/instructions/testing.instructions.md`:

- No `print()`, no bare `except:`, no `.env` reliance.
- Mandatory docstrings on file and every test class.
- Pairing rule: the test must import the source module OR share its filename stem so the coverage gate accepts it.

## Step 7 — Append to the test-plan

After writing the test file, append a row for the new batch to the appropriate cluster section in `test-writing-plan.md`. Use the same table shape as the most recent batch in that cluster. Fill in:

- Scenario range
- Type letter
- Files covered
- Test file path
- Test classes
- Fixtures
- Tests landed: leave as `pending — to be measured after run`
- Coverage gain: leave as `pending — to be measured after coverage run`
- Status: ⏳ In progress

## Step 8 — Suggest next steps

Output:

```
Done. Suggested next actions:
  1. Run the new tests:  pytest <file> -v
  2. Run with coverage:  pytest --cov=lex.lex_app <file>
  3. When green: commit with message "Add cluster <NN><letter> — <short>" and open a PR against lex-app-v2.
  4. If this is the test PR for a coverage-task issue, change the PR base to the parent PR's head branch (not lex-app-v2) — see testing.instructions §9.
```

## What NOT to do

- Do not pick a cluster letter without reading `test-writing-plan.md` first.
- Do not skip the confirmation step in Step 5.
- Do not invent new clusters or renumber existing ones.
- Do not write tests for a source file already slotted in an in-flight batch — defer to that batch.
- Do not touch source files in the same PR as the test scaffolding. Tests-only PRs unless explicitly bundled by the user.
