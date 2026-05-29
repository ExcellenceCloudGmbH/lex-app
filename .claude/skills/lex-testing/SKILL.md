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

## Step 0 — Research the intent first (the Golden Rule — never skip)

**Before writing a single line of source or test, gather enough context to solve the problem
correctly.** The source code is an incomplete story — it has bugs and workarounds. A test derived by
mirroring the implementation locks those bugs in. This is the single most common failure mode here.
Do this yourself, automatically — do not wait to be asked:

1. **Read the docs that describe intent**, not just the code: [`docs/features/`](../../../docs/features/),
   [`docs/reference/`](../../../docs/reference/), [`docs/tutorial/`](../../../docs/tutorial/). They say what the
   framework is *trying to achieve*. Derive the behaviour you'll implement and test from that intent
   + *"What would a customer reasonably expect?"* — not from what the code currently happens to do.
2. **Read the public API docstrings** of the classes/functions you'll touch, and skim the existing
   cluster tests for that topic to match established patterns.
3. **Check for an existing mechanism before inventing one.** E.g. cross-process state already lives
   in the cache-backed `ActiveCalculationStateStore` — don't add a process-local dict that fails
   silently across Celery workers. Search the codebase for the capability first.
4. **If the request is ambiguous or has more than one defensible design, STOP and ask the
   developer** before coding — surface the trade-offs and let them choose. Use the
   `superpowers:brainstorming` skill. A wrong assumption baked into a feature + its tests costs far
   more than one question.

Only once you understand the intent do you proceed to allocation. If a test then fails because the
code is genuinely buggy, that is the test doing its job — record it (Step 7), don't weaken it.

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

**Location is strict:** the file goes in the cluster tree —
`lex/test_project/tests/<cluster_slug>/test_<NN><letter>_<short>.py`. **Never** drop a feature test
into the legacy `lex/tests/unit/`, `lex/tests/integration/`, or `lex/tests/e2e/` trees — those are
the pre-existing audit suite and adding feature tests there bypasses the cluster plan and
coverage-task tracking. That escape hatch is exactly what breaks plan consistency.

Create the file with this header and follow every rule in `testing.instructions.md` (no `print()`,
no bare `except:`, no `.env` reliance, real DB models — mock only external boundaries, mandatory
docstrings). The module header carries an `Intent` section (what the framework is trying to achieve
+ why a regression matters + the scenario range) and a module-level
`pytestmark = pytest.mark.<cluster_slug>`:

```python
"""<one-line summary of what these tests cover>.

Intent: <what the framework is trying to achieve here + why a regression matters>.
Cluster <NN><letter> — scenarios <start>–<end>. Type: <U | I | E>.
Covers: <files covered>.
Run: python -m lex pytest lex/test_project/tests/<cluster_slug>/test_<NN><letter>_<short>.py -v
"""

import pytest

pytestmark = pytest.mark.<cluster_slug>


class TestCluster<NN><letter>_<Description>(<SimpleTestCase | TestCase | APITestCase | E2ETestCase>):
    """Cluster <NN><letter>: <description>."""

    def test_<NN>_<NN>_<behaviour>(self):
        """
        Scenario <X>.<Y>: <one-line description>
        Given: <setup>
        When: <action>
        Then: <expected outcome — derived from docs/intent, not from the implementation>
        """
        ...  # every assertion carries a human-readable failure message
```

**Coverage pairing (critical):** the test must import the changed source module OR share its
filename stem, or the coverage gate rejects it. This is the local mirror of the cloud paradigm —
the gate would otherwise open a `coverage-task` issue and assign Copilot. Pre-empt that here.

## Step 7 — Definition of Done: bring the plan into sync (mandatory)

**The task is NOT done until the test-plan on disk matches the tests you wrote.** Writing the test
is only half the job. This mirrors the **cloud Copilot agent's required deliverables**
(`.github/scripts/copilot_assemble_prompt.py`) so both paths stay consistent — do all of this in the
**same change**, not as optional follow-up:

1. **Append a row to `progress/session-log.md`** — the universal per-PR record (its header: "the
   Copilot test-bot writes here as part of every PR"). Append-only, bottom row, never re-order.

2. **Update the cluster status / scenario range** in `test-clusters.md` for each touched
   (sub-)cluster, and bump the matching row in `progress/dashboard.md`.

3. **If the work maps to a planned batch, append/update the batch row** in `test-writing-plan.md`,
   matching the most recent batch's table shape (scenario range, type U/I/E, files covered, test
   file path, test classes, fixtures, status). After you run the suite (Step 8), record the **real**
   results (`N pass / 0 fail`, measured coverage gain) in the rows above and flip *Status* to
   ✅ Complete. Don't leave `pending` placeholders in a finished change.

4. **If a test exposes a real framework bug, record it — don't weaken the test.** Per the
   `known-bugs.md` workflow: assert the *correct* behaviour, mark the test
   `@unittest.expectedFailure` (or `@pytest.mark.xfail(strict=True)` for post-cutover tests), and
   add a `BUG-NNN` row (description, severity, cluster, test, status). The marker is dropped when
   the framework is fixed; the test becomes a live regression gate. Never soften an assertion to
   make a real bug pass.

If the plan and the tests on disk disagree, you are not done.

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

- Start writing code or tests before the Step 0 research (docs for intent) — and never derive a
  test by mirroring the implementation.
- Put a feature test in the legacy `lex/tests/unit/`, `integration/`, or `e2e/` trees — cluster
  tests go in `lex/test_project/tests/<cluster_slug>/`.
- Pick a cluster letter without reading `test-writing-plan.md` first.
- Skip the Step 5 confirmation.
- Invent new clusters or renumber existing ones.
- Write tests for a source file already slotted in an in-flight batch.
- Finish without bringing `test-writing-plan.md` into sync with the tests on disk.
- Soften an assertion to hide a real bug — record it in `known-bugs.md` instead.
