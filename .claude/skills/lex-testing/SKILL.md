---
name: lex-testing
description: Use when writing or modifying tests for the lex-app framework, or when changing framework source under lex/ (which requires paired cluster tests in the same change). Allocates the correct cluster/letter/scenario from the test-plan, scaffolds the test, and keeps test-writing-plan.md and known-bugs.md in sync. Mirrors the cloud Copilot coverage-gate paradigm.
---

# LEX cluster testing

You are writing tests for the lex-app framework. This work is **release-gating** and the test
suite doubles as living documentation, so the test-plan in `lex/test_project/test-plan/` is
followed **strictly — never improvise** cluster names, letters, or scenario IDs.

The full prose rules are in [`.github/instructions/testing.instructions.md`](../../../.github/instructions/testing.instructions.md).
This skill is the executable workflow for cluster-aligned tests.

## When this applies

- You added or changed framework source under `lex/` → you write the paired test in the **same
  change** (the CI coverage gate enforces it — see step 6). You do not wait to be asked.
- The user asks for a test for a specific module/behaviour.

If the change is to a downstream Lex *project* (not this framework repo), the cluster rules do
**not** apply — follow that project's conventions instead.

## Step 1 — Read the test-plan (always, before allocating)

Read these before doing anything else; if any is missing, **stop and tell the user** — do not guess:

1. [`lex/test_project/test-plan/index.md`](../../../lex/test_project/test-plan/index.md) — overview.
2. [`lex/test_project/test-plan/test-clusters.md`](../../../lex/test_project/test-plan/test-clusters.md) — cluster→topic mapping.
3. [`lex/test_project/test-plan/test-writing-plan.md`](../../../lex/test_project/test-plan/test-writing-plan.md) — current batch state, in-flight clusters, allocation rules.
4. [`lex/test_project/test-plan/known-bugs.md`](../../../lex/test_project/test-plan/known-bugs.md) — bug-recording workflow.

## Step 2 — Identify the cluster

Map the source module to a cluster using `test-clusters.md`. Common mappings:

| Source area | Cluster |
| --- | --- |
| `lex/lex_app/__init__.py`, settings, urls, views, bootstrap | 1 |
| `lex/lex_app/LexModel.py`, ORM models, permissions | 3, 4 |
| Audit logging | 6 |
| Calculation models, calculation flow | 7 |
| Celery / async / workers | 8 |
| WebSocket consumers | 9 |
| Serializers, REST | 2, 12 |
| Model export / import | 13 |
| List views, AG Grid | 14 |

If the mapping is ambiguous, ask the user which cluster. Don't pick one blindly.

## Step 3 — Allocate the next free letter and scenario range

From `test-writing-plan.md`:

1. List every batch already documented for the target cluster (e.g. 1a … 1o, 1p).
2. Next free letter = next alphabetical letter after the highest in use. **Letters are never renumbered.**
3. Find the cluster's current max scenario ID; the new batch starts at `max + 1`.
4. Check "Pending decisions" and in-flight Tier-A clusters — if the source file is already slotted in an in-flight batch, **stop and tell the user**. Do not duplicate.

## Step 4 — Determine the test type letter

| Letter | Class | When |
| --- | --- | --- |
| **U** | `SimpleTestCase` / plain pytest function | Pure logic, no DB. **Prefer this.** |
| **I** | `TestCase` | ORM access, per-test transaction rollback |
| **E** | `APITestCase` + `APIClient` | REST endpoint testing |

Default to **U** unless the test clearly needs DB or HTTP plumbing.

## Step 5 — Confirm the allocation before scaffolding

Output exactly this and wait for confirmation:

```
Allocating new batch:
  Cluster:        <NN><letter>          (e.g. 7n)
  Scenarios:      <start> – <end>       (e.g. 7.166 – 7.178)
  Type:           U | I | E
  Test file:      lex/test_project/tests/<topic>/test_<NN><letter>_<short>.py
  Test classes:   TestCluster<NN><letter>_<Description>
  Files covered:  <source files>
  Fixtures:       <list, or "none">

Confirm? (yes / change cluster / change type)
```

Revise and re-confirm if the user wants changes. Don't proceed without confirmation.

## Step 6 — Scaffold the test file

Create the file with this header and follow every rule in `testing.instructions.md` (no `print()`,
no bare `except:`, no `.env` reliance, real DB models — mock only external boundaries, mandatory
docstrings):

```python
"""<one-line summary of what these tests cover>.

Cluster <NN><letter> — scenarios <start>–<end>. Type: <U | I | E>.
Covers: <files covered>.
Run: python -m lex pytest lex/test_project/tests/<topic>/test_<NN><letter>_<short>.py -v
"""
```

**Coverage pairing (critical):** the test must import the changed source module OR share its
filename stem, or the coverage gate rejects it. This is the local mirror of the cloud paradigm —
the gate would otherwise open a `coverage-task` issue and assign Copilot. Pre-empt that here.

## Step 7 — Keep the plan honest (mandatory)

1. **Append the batch row** to the right cluster section in `test-writing-plan.md`, matching the
   most recent batch's table shape. Set *Tests landed* and *Coverage gain* to
   `pending — measured after run`; update them once you've run the suite, then flip *Status* to
   ✅ Complete.

2. **If a test exposes a real framework bug, record it — don't weaken the test.** Per the
   `known-bugs.md` workflow: assert the *correct* behaviour, mark the test
   `@unittest.expectedFailure`, and add a `BUG-NNN` row (description, severity, cluster, test,
   status). The marker is dropped when the framework is fixed; the test becomes a live regression
   gate. Never soften an assertion to make a real bug pass.

## Step 8 — Run and report

```
Done. Next:
  1. Run:           python -m lex pytest <file> -v
  2. With coverage: python -m lex pytest --cov=lex <file>
  3. When green:    commit (source + test + plan row together) and open a PR against lex-app-v2.
  4. If this closes a coverage-task issue: set the PR base to the parent PR's head branch
     (NOT lex-app-v2) and end the body with `Fixes #<issue>` — see testing.instructions §9.
```

## Never

- Pick a cluster letter without reading `test-writing-plan.md` first.
- Skip the Step 5 confirmation.
- Invent new clusters or renumber existing ones.
- Write tests for a source file already slotted in an in-flight batch.
- Soften an assertion to hide a real bug — record it in `known-bugs.md` instead.
