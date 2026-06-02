# Copilot Test-Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up an issue-driven Copilot pipeline that authors tests against `lex/test_project/test-plan/` conventions, gates the resulting PR, auto-merges low-risk modes, and optionally cuts a draft release.

**Architecture:** Three event-driven workflows in `lex-app` (entry, PR-gate, post-merge publish) backed by three small Python helpers in `.github/scripts/` (each unit-tested with pytest). The entry workflow assembles the Copilot prompt at runtime by reading `lex/test_project/test-plan/` files so docs edits propagate without YAML churn. `progress.md` is decomposed first (`progress/conventions.md`, `progress/session-log.md`, `progress/dashboard.md`) so high-churn append surfaces are isolated.

**Tech Stack:** GitHub Actions (YAML), Python 3.11+ (`from __future__ import annotations`, dataclasses), pytest for helper unit tests, `gh` CLI for repo ops, GitHub Copilot coding agent (`copilot-swe-agent[bot]`).

---

## File Structure

```
.github/workflows/
  copilot_test_bot.yml                 (NEW — entry; on issues:[labeled] + workflow_dispatch)
  copilot_pr_gate.yml                  (NEW — gates Copilot PRs; on pull_request)
  copilot_publish_after_merge.yml      (NEW — optional draft release; on pull_request:closed)

.github/ISSUE_TEMPLATE/
  copilot-test-request.yml             (NEW — issue form with mode label + behaviour fields)

.github/scripts/
  copilot_assemble_prompt.py           (NEW — reads test-plan docs + issue body → prompt)
  copilot_validate_pr_shape.py         (NEW — runs the §7 PR-shape check matrix)
  copilot_compute_next_rc.py           (NEW — bumps `vX.Y.ZrcN → vX.Y.Zrc(N+1)`)
  tests/                               (NEW — pytest dir for the three scripts)
    __init__.py
    test_copilot_assemble_prompt.py
    test_copilot_validate_pr_shape.py
    test_copilot_compute_next_rc.py

lex/test_project/test-plan/
  progress.md                          (REWRITTEN — thin index pointing to progress/)
  progress/                            (NEW directory)
    conventions.md                     (extracted: methodology, organization, UX, how-to-run, quality gates)
    session-log.md                     (extracted Session Log + content of both stale siblings, deduped)
    dashboard.md                       (extracted: per-cluster table + KPI ratchet rules)
  session-log.md                       (DELETED after content merged into progress/session-log.md)
  progress-session-log.md              (DELETED after content merged into progress/session-log.md)

docs/ci-cd/
  copilot-test-bot.md                  (NEW — user-facing how-it-works doc; mirrors automated-docs-pipeline.md)
```

---

## Task 1: Extract `progress/conventions.md` from `progress.md`

**Files:**
- Create: `lex/test_project/test-plan/progress/conventions.md`
- Modify: none yet (the source `progress.md` is rewritten in Task 4 after all extractions are done)

The current `progress.md` mixes a high-churn dashboard table + Session Log with stable methodology/UX/run-instructions. `progress/conventions.md` owns the stable bits the Copilot prompt will reference.

- [ ] **Step 1: Create `progress/` directory and the conventions file with verbatim extracted content**

The new file lifts the following sections from the current `lex/test_project/test-plan/progress.md` verbatim (no rewriting — the wording is the contract Copilot follows):

- Section "How We Organize the Work" (rules: cluster order, one cluster at a time, test intent / never overfit, test-first-then-fix) — lines ~93-127 of current `progress.md`.
- Section "User Experience: Making Tests Readable" (naming convention, assertion messages, test class organization) — lines ~130-191.
- Section "How to Run Tests" — lines ~195-212.
- Section "Quality Gates" — lines ~275-285.

Write `lex/test_project/test-plan/progress/conventions.md` with this layout:

```markdown
# Test-Plan Conventions

> **Back to:** [Progress index](../progress.md) | [Test Plan Index](../index.md)
> **Audience:** anyone authoring a new test (human or Copilot) — the stable rules that don't change per session.

This file owns the methodology, naming, and quality gates. The high-churn per-cluster status lives in [`dashboard.md`](dashboard.md); the per-session narrative lives in [`session-log.md`](session-log.md).

---

## How We Organize the Work

<verbatim copy of the four "Rule:" subsections from progress.md — Work in Cluster Order, One Cluster at a Time, Test Intent Never Overfit, Test First Then Fix>

---

## User Experience: Making Tests Readable

<verbatim copy of the Naming Convention, Assertion Messages, Test Class Organization subsections>

---

## How to Run Tests

<verbatim copy of the three code blocks: Run all clusters, Run a single cluster, Run with coverage>

---

## Quality Gates

<verbatim copy of the six numbered gates>

---

> **Back to:** [Progress index](../progress.md) | **See also:** [Test Clusters](../test-clusters.md) | [Dashboard](dashboard.md) | [Session Log](session-log.md)
```

The Copilot prompt-assembly script (Task 7) reads exactly this file for the conventions block, so the section headings above are load-bearing — keep them.

- [ ] **Step 2: Verify the file renders and links resolve**

Run: `ls lex/test_project/test-plan/progress/conventions.md && head -5 lex/test_project/test-plan/progress/conventions.md`
Expected: file exists; first heading is `# Test-Plan Conventions`.

- [ ] **Step 3: Commit**

```bash
git add lex/test_project/test-plan/progress/conventions.md
git commit -m "docs(test-plan): extract stable methodology + UX + run + gates into progress/conventions.md"
```

---

## Task 2: Extract `progress/session-log.md` from the two stale siblings

**Files:**
- Create: `lex/test_project/test-plan/progress/session-log.md`
- Read (no modifications yet — deletes happen in Task 5): `lex/test_project/test-plan/session-log.md`, `lex/test_project/test-plan/progress-session-log.md`

The repo has two stale sibling files (`session-log.md`, `progress-session-log.md`) that look like earlier split attempts. `progress-session-log.md` has the full chronological narrative (sessions 1+, multi-paragraph "What Was Done"); `session-log.md` is the header-only stub. Single source of truth lives in `progress/session-log.md`.

- [ ] **Step 1: Build the merged file**

The merge is mechanical: take `progress-session-log.md` verbatim as the base (it has the full table + every row), then prepend the header from the existing stub `session-log.md` if it adds anything (it doesn't — the stub's header is a subset). After confirming the stub adds nothing new, the merge is just "copy `progress-session-log.md` to `progress/session-log.md` with the back-link rewritten".

Write `lex/test_project/test-plan/progress/session-log.md`:

```markdown
# Session Log

> **Back to:** [Progress index](../progress.md) | [Test Plan Index](../index.md)
>
> **What this is:** the chronological narrative of every test-plan work session — what was done, what changed, what surfaced.
>
> **Append-only.** Add new rows at the bottom; never re-order, never re-number. The Copilot test-bot writes here as part of every PR (see [`docs/ci-cd/copilot-test-bot.md`](../../../../docs/ci-cd/copilot-test-bot.md)).

---

Record each work session here so progress is traceable.

| Date | Session | What Was Done | Clusters Affected | Tests Added | Tests Passing |
|------|---------|---------------|-------------------|-------------|---------------|
<every row from progress-session-log.md verbatim, in original order>
```

The verbatim row block is copied with `cat` into the body — preserving the table is the whole point.

- [ ] **Step 2: Sanity-check row count matches source**

Run:
```bash
diff <(grep -c '^|' lex/test_project/test-plan/progress-session-log.md) \
     <(grep -c '^|' lex/test_project/test-plan/progress/session-log.md)
```
Expected: zero diff. If non-zero, a row was dropped during paste — fix before continuing.

- [ ] **Step 3: Commit**

```bash
git add lex/test_project/test-plan/progress/session-log.md
git commit -m "docs(test-plan): seed progress/session-log.md from progress-session-log.md (single source of truth)"
```

---

## Task 3: Extract `progress/dashboard.md` from the dashboard + bugs tables

**Files:**
- Create: `lex/test_project/test-plan/progress/dashboard.md`

The dashboard owns the per-cluster status table (lines 14-69 of current `progress.md`), the status legend (~71-75), the "What Counts as Done" subsection (~79-89), and the Known Bugs Tracker (~216-244). These three blocks change together every session, so they live in the same file.

- [ ] **Step 1: Write the dashboard file**

Write `lex/test_project/test-plan/progress/dashboard.md`:

```markdown
# Test-Suite Dashboard

> **Back to:** [Progress index](../progress.md) | [Test Plan Index](../index.md)
> **Audience:** Engineering leadership, QA supervisors, anyone scanning suite health at a glance.
> **Update cadence:** after every work session — both the per-cluster table AND the Known Bugs Tracker. The Copilot test-bot updates only the cluster row(s) it touched and (in modes B/C) appends a Known-Bugs row.

---

## At a glance

<verbatim copy of the per-cluster table from progress.md lines 14-69>

**Status legend:**

<verbatim copy of the four-bullet legend from lines 72-75>

---

## What Counts as "Done" for a Cluster

<verbatim copy of the five numbered criteria from lines 81-89>

---

## Known Bugs Tracker

Bugs discovered by the new test suite. Each has a corresponding test marked `@unittest.expectedFailure`.

<verbatim copy of the BUG-NNN table header + every row from lines 220-244>

---

> **Back to:** [Progress index](../progress.md) | **See also:** [Conventions](conventions.md) | [Session Log](session-log.md)
```

- [ ] **Step 2: Sanity-check row counts match source**

Run:
```bash
# Dashboard table: original has 56 cluster rows + header + separator
grep -c '^| ' lex/test_project/test-plan/progress/dashboard.md
```
Expected: matches the count from the source file's lines 14-69 + 220-244 sections (record the exact number after extracting; a regression here means a cluster row was dropped).

- [ ] **Step 3: Commit**

```bash
git add lex/test_project/test-plan/progress/dashboard.md
git commit -m "docs(test-plan): split dashboard table + KPIs + Known Bugs Tracker into progress/dashboard.md"
```

---

## Task 4: Rewrite `progress.md` as a thin index

**Files:**
- Modify: `lex/test_project/test-plan/progress.md` (replace ~288 lines with ~30 lines)

After Tasks 1-3, every block of `progress.md` content has been re-homed. The original file becomes a navigation page that points readers (and the Copilot prompt) at the three child files. Keep the path so any external links still resolve.

- [ ] **Step 1: Replace the file with the index version**

Write `lex/test_project/test-plan/progress.md`:

```markdown
# Progress & Organization

> **Back to:** [Test Plan Index](index.md)
> **Decoupled May 2026** — see the per-file links below.

This page is now an index. The actual content is split across three files in [`progress/`](progress/):

| File | Owns | Update cadence |
|------|------|----------------|
| [`progress/conventions.md`](progress/conventions.md) | Methodology, naming, run-instructions, Quality Gates | Stable — edit only when a rule changes |
| [`progress/dashboard.md`](progress/dashboard.md) | Per-cluster status table, KPIs, Known Bugs Tracker | Every session — touch one row + (sometimes) one bug row |
| [`progress/session-log.md`](progress/session-log.md) | Chronological per-session narrative | Append-only — one row per session, never re-order |

## Why split?

The dashboard table and the Known Bugs Tracker get touched on almost every session; the conventions and the session log don't. Keeping them in one ~290-line file made every session change a merge-conflict candidate, and made the Copilot test-bot's "append one row" discipline impossible to enforce mechanically. The split mirrors the volatility, so each PR touches the smallest file.

## For the Copilot test-bot

The PR-gate workflow (`copilot_pr_gate.yml`) checks that test-bot PRs:

- modify exactly one row in [`progress/dashboard.md`](progress/dashboard.md) (the row for the touched cluster), or none if the touched cluster is brand-new (in which case a new row is appended at the bottom);
- append exactly one row to [`progress/session-log.md`](progress/session-log.md), at the bottom;
- never modify [`progress/conventions.md`](progress/conventions.md) — methodology changes are human-driven.

See [`docs/ci-cd/copilot-test-bot.md`](../../../docs/ci-cd/copilot-test-bot.md) for the full mechanism.

---

> **Back to:** [Test Plan Index](index.md) | **See also:** [Test Clusters](test-clusters.md) | [Expected Results](expected-results.md)
```

- [ ] **Step 2: Verify all internal links resolve**

Run: `grep -oE '\]\([^)]+\)' lex/test_project/test-plan/progress.md`
Then for each relative link, confirm the target file exists.

- [ ] **Step 3: Commit**

```bash
git add lex/test_project/test-plan/progress.md
git commit -m "docs(test-plan): rewrite progress.md as a thin index pointing to progress/{conventions,dashboard,session-log}.md"
```

---

## Task 5: Delete the two stale sibling files

**Files:**
- Delete: `lex/test_project/test-plan/session-log.md`
- Delete: `lex/test_project/test-plan/progress-session-log.md`

After Task 2 merged their content into `progress/session-log.md`, the siblings are dead links waiting to mislead.

- [ ] **Step 1: Confirm no other file references either of them**

Run: `grep -rn -e 'progress-session-log\.md' -e 'test-plan/session-log\.md' --exclude-dir=.git .`
Expected: zero hits outside the two files themselves and the historical session-log narrative inside `progress/session-log.md`. If a doc still links to either file, update the link to `progress/session-log.md` first.

- [ ] **Step 2: Delete both files**

Run:
```bash
git rm lex/test_project/test-plan/session-log.md lex/test_project/test-plan/progress-session-log.md
```

- [ ] **Step 3: Commit**

```bash
git commit -m "docs(test-plan): drop stale session-log.md / progress-session-log.md siblings (single source of truth in progress/session-log.md)"
```

---

## Task 6: Issue template + label set

**Files:**
- Create: `.github/ISSUE_TEMPLATE/copilot-test-request.yml`
- Create (no file — repo settings): the seven labels in §10 of the spec

The template is the single human-facing entry point. The labels are the wire-protocol that the workflows read.

- [ ] **Step 1: Write the issue template**

Write `.github/ISSUE_TEMPLATE/copilot-test-request.yml`:

```yaml
name: Copilot Test Request
description: Ask the Copilot test-bot to author (and optionally fix) a test for a behaviour or bug.
title: "[copilot-test] "
labels: []
body:
  - type: markdown
    attributes:
      value: |
        Filing this issue triggers `copilot_test_bot.yml`.
        See [`docs/ci-cd/copilot-test-bot.md`](../../docs/ci-cd/copilot-test-bot.md) for the full mechanism.

  - type: dropdown
    id: mode
    attributes:
      label: Mode (REQUIRED — drives the workflow's behaviour)
      description: |
        - **regression** — codify a behaviour that already works. PR auto-merges on green CI.
        - **bug-repro** — write an `@expectedFailure` test that reproduces a known bug; appends a row to `known-bugs.md`. PR auto-merges on green CI.
        - **fix-and-test** — write a failing test, then make the smallest source change to pass it. **Human review required, no auto-merge.**
      options:
        - regression
        - bug-repro
        - fix-and-test
    validations:
      required: true

  - type: textarea
    id: behaviour
    attributes:
      label: Behaviour description
      description: What is the framework supposed to do? Describe the contract — not the current code.
      placeholder: |
        e.g. "When a CalculatedModel.create() call hits an IntegrityError on save, the framework
        should call delete_models_with_same_defining_fields, rewire the pk, and retry the save.
        The conflicts_resolved counter should bump by one per resolved conflict."
    validations:
      required: true

  - type: textarea
    id: reproducer
    attributes:
      label: Reproducer / steps
      description: REQUIRED for bug-repro and fix-and-test. Optional for regression.
      placeholder: |
        1. Set up X
        2. Call Y
        3. Observe Z (bug) — should be W
    validations:
      required: false

  - type: input
    id: cluster_hint
    attributes:
      label: Cluster hint (optional)
      description: |
        - existing letter (`7g`, `12e`) → use that sub-cluster
        - cluster number only (`7`) → next free letter inside it
        - `new` → create a new cluster
        - `others` or blank → place under `others/` if no existing cluster fits

  - type: input
    id: files
    attributes:
      label: Files involved (optional)
      description: Source files Copilot should look at for context. Comma-separated paths.

  - type: checkboxes
    id: publish
    attributes:
      label: Publish on merge
      description: If checked AND the bot's PR auto-merges AND the repo variable `COPILOT_AUTO_PUBLISH_ENABLED` is `"true"`, cuts a draft rc release.
      options:
        - label: Cut a draft `rc` release after merge
```

- [ ] **Step 2: Manual repo-config checklist (NOT automatable from this PR)**

The workflow assumes the seven labels exist. Document them in the user-facing doc (Task 13) and call them out in the PR description for this commit so the maintainer creates them at the same time:

- `copilot:regression`
- `copilot:bug-repro`
- `copilot:fix-and-test`
- `copilot:invalid`
- `auto-merge`
- `needs-human-review`
- `publish-on-merge`

Plus repo variable `COPILOT_AUTO_PUBLISH_ENABLED` (default `"false"`).
Plus enabling Copilot coding agent on `lex-app` at the org Copilot policy level.
Plus the branch-protection bot bypass for `copilot-swe-agent[bot]`.

- [ ] **Step 3: Commit**

```bash
git add .github/ISSUE_TEMPLATE/copilot-test-request.yml
git commit -m "feat(ci): add Copilot test-request issue template (mode label + behaviour + reproducer + cluster hint)"
```

---

## Task 7: `copilot_assemble_prompt.py` — write failing pytest first

**Files:**
- Create: `.github/scripts/tests/__init__.py`
- Create: `.github/scripts/tests/test_copilot_assemble_prompt.py`

TDD: assert the assembled prompt's contract before writing the script. The contract is what the rest of the workflow depends on — section markers, mode-block selection, issue-data substitution.

- [ ] **Step 1: Create the empty pytest package marker**

Write `.github/scripts/tests/__init__.py`:

```python
```

- [ ] **Step 2: Write the failing tests**

Write `.github/scripts/tests/test_copilot_assemble_prompt.py`:

```python
"""Tests for copilot_assemble_prompt.assemble_prompt — contract pinned before script lands."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

# Make .github/scripts importable when pytest runs from repo root.
SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from copilot_assemble_prompt import IssueInput, Mode, assemble_prompt  # noqa: E402


@pytest.fixture
def fake_test_plan(tmp_path: Path) -> Path:
    """Build a minimal test-plan/ tree with the four files the assembler reads."""
    plan = tmp_path / "test-plan"
    (plan / "progress").mkdir(parents=True)
    (plan / "index.md").write_text(
        "# Index\n\n## Golden Rule\n\nTest what the framework is **trying** to achieve, "
        "not what the current code happens to do.\n"
    )
    (plan / "test-clusters.md").write_text("# Clusters\n\nCluster 7 — calculation state machine.\n")
    (plan / "test-writing-plan.md").write_text(
        "# Writing plan\n\nFile naming: `tests/<cluster>/test_<Nx>_<slug>.py`.\n"
    )
    (plan / "progress" / "conventions.md").write_text(
        "# Conventions\n\nAppend rows to `progress/session-log.md`.\n"
    )
    return plan


def _basic_issue(mode: Mode) -> IssueInput:
    return IssueInput(
        number=42,
        title="Add regression test for X",
        mode=mode,
        behaviour="Framework should reset state on success.",
        reproducer="1. Do A\n2. Observe B",
        cluster_hint="7g",
        files=["lex/foo.py"],
    )


def test_assembled_prompt_includes_golden_rule_block(fake_test_plan: Path) -> None:
    out = assemble_prompt(_basic_issue(Mode.REGRESSION), test_plan_dir=fake_test_plan)
    assert "Golden Rule" in out
    assert "trying" in out


def test_assembled_prompt_includes_per_mode_instruction_block(fake_test_plan: Path) -> None:
    out_reg = assemble_prompt(_basic_issue(Mode.REGRESSION), test_plan_dir=fake_test_plan)
    out_bug = assemble_prompt(_basic_issue(Mode.BUG_REPRO), test_plan_dir=fake_test_plan)
    out_fix = assemble_prompt(_basic_issue(Mode.FIX_AND_TEST), test_plan_dir=fake_test_plan)
    # Each mode injects a distinct, identifiable header.
    assert "## Mode: regression" in out_reg
    assert "## Mode: bug-repro" in out_bug
    assert "## Mode: fix-and-test" in out_fix
    # Mode-B uniquely instructs the @expectedFailure decorator + known-bugs.md row.
    assert "@unittest.expectedFailure" in out_bug
    assert "known-bugs.md" in out_bug
    # Mode-C uniquely allows source edits.
    assert "smallest source change" in out_fix


def test_assembled_prompt_substitutes_issue_data(fake_test_plan: Path) -> None:
    out = assemble_prompt(_basic_issue(Mode.REGRESSION), test_plan_dir=fake_test_plan)
    assert "Add regression test for X" in out
    assert "Framework should reset state on success." in out
    assert "1. Do A" in out
    assert "7g" in out
    assert "lex/foo.py" in out


def test_assembled_prompt_section_order_is_stable(fake_test_plan: Path) -> None:
    """Golden Rule must come first so Copilot reads it before anything else."""
    out = assemble_prompt(_basic_issue(Mode.REGRESSION), test_plan_dir=fake_test_plan)
    golden_pos = out.index("Golden Rule")
    mode_pos = out.index("## Mode: regression")
    issue_pos = out.index("Add regression test for X")
    assert golden_pos < mode_pos < issue_pos


def test_missing_test_plan_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        assemble_prompt(_basic_issue(Mode.REGRESSION), test_plan_dir=tmp_path / "nope")


def test_blank_reproducer_allowed_for_regression(fake_test_plan: Path) -> None:
    issue = IssueInput(
        number=1, title="t", mode=Mode.REGRESSION, behaviour="b",
        reproducer="", cluster_hint="", files=[],
    )
    out = assemble_prompt(issue, test_plan_dir=fake_test_plan)
    assert "Reproducer" in out  # heading still present
    # Body says "(none provided)" so Copilot doesn't get a silently empty section.
    assert "(none provided)" in out


def test_blank_reproducer_rejected_for_bug_repro(fake_test_plan: Path) -> None:
    issue = IssueInput(
        number=1, title="t", mode=Mode.BUG_REPRO, behaviour="b",
        reproducer="", cluster_hint="", files=[],
    )
    with pytest.raises(ValueError, match="reproducer is required"):
        assemble_prompt(issue, test_plan_dir=fake_test_plan)


def test_blank_reproducer_rejected_for_fix_and_test(fake_test_plan: Path) -> None:
    issue = IssueInput(
        number=1, title="t", mode=Mode.FIX_AND_TEST, behaviour="b",
        reproducer="", cluster_hint="", files=[],
    )
    with pytest.raises(ValueError, match="reproducer is required"):
        assemble_prompt(issue, test_plan_dir=fake_test_plan)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest .github/scripts/tests/test_copilot_assemble_prompt.py -v`
Expected: ImportError (`No module named 'copilot_assemble_prompt'`) — confirms TDD red phase.

---

## Task 8: `copilot_assemble_prompt.py` — implementation + workflow

**Files:**
- Create: `.github/scripts/copilot_assemble_prompt.py`
- Create: `.github/workflows/copilot_test_bot.yml`

- [ ] **Step 1: Implement the script**

Write `.github/scripts/copilot_assemble_prompt.py`:

```python
"""Assemble the Copilot-task issue body from test-plan docs + the source issue.

Read at runtime by ``copilot_test_bot.yml``. Reads four files from
``lex/test_project/test-plan/`` so docs edits propagate without YAML churn.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Mode(str, Enum):
    REGRESSION = "regression"
    BUG_REPRO = "bug-repro"
    FIX_AND_TEST = "fix-and-test"


@dataclass
class IssueInput:
    number: int
    title: str
    mode: Mode
    behaviour: str
    reproducer: str
    cluster_hint: str
    files: list[str] = field(default_factory=list)


_GOLDEN_RULE_ANCHOR = "## Golden Rule"

_MODE_BLOCKS: dict[Mode, str] = {
    Mode.REGRESSION: """
## Mode: regression

Write a **passing** test that codifies the documented behaviour. No source changes. The test must pass against current code on its first run; the PR-gate workflow runs it in isolation.
""".strip(),
    Mode.BUG_REPRO: """
## Mode: bug-repro

Write a test decorated with `@unittest.expectedFailure` that reproduces the bug. Append exactly one new row to `lex/test_project/test-plan/known-bugs.md` describing the bug (severity / cluster / test reference). Mention the new BUG-NNN id in the test docstring. The PR-gate workflow strips the decorator on a temp copy and asserts the test FAILS — if your test passes without the decorator, the bug is not reproducible and the PR will be blocked.
""".strip(),
    Mode.FIX_AND_TEST: """
## Mode: fix-and-test

Write a failing test for the documented behaviour, then make the **smallest** source change that makes it pass. Source diff must be ≤ 50 changed lines. List every source file you touched in the PR description under a `### Source changes` heading with one bullet per file and a one-line rationale. **No auto-merge** — this PR is routed to human review.
""".strip(),
}


def _read(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"required test-plan file missing: {path}")
    return path.read_text()


def assemble_prompt(issue: IssueInput, *, test_plan_dir: Path) -> str:
    """Build the markdown body for the Copilot-task issue.

    Section order is load-bearing: Golden Rule first (anti-overfitting discipline
    before everything else), then conventions, clusters, writing-plan, then the
    mode block, then the issue itself.
    """
    if issue.mode in (Mode.BUG_REPRO, Mode.FIX_AND_TEST) and not issue.reproducer.strip():
        raise ValueError(f"reproducer is required for mode {issue.mode.value}")

    index_md = _read(test_plan_dir / "index.md")
    clusters_md = _read(test_plan_dir / "test-clusters.md")
    writing_md = _read(test_plan_dir / "test-writing-plan.md")
    conventions_md = _read(test_plan_dir / "progress" / "conventions.md")

    # Pull the Golden Rule paragraph out of index.md so it's front-loaded.
    if _GOLDEN_RULE_ANCHOR in index_md:
        golden = index_md.split(_GOLDEN_RULE_ANCHOR, 1)[1].split("\n## ", 1)[0]
        golden_block = f"{_GOLDEN_RULE_ANCHOR}{golden}".rstrip()
    else:
        # Fallback: include the whole index.md so the rule is at least present.
        golden_block = index_md.rstrip()

    files_block = "\n".join(f"- `{f}`" for f in issue.files) if issue.files else "(none provided)"
    reproducer_block = issue.reproducer.strip() or "(none provided)"
    hint_block = issue.cluster_hint.strip() or "(blank — fall back to cluster routing rules)"

    return f"""\
# {issue.title}

> Copilot task issue assembled from #{issue.number}. Read the Golden Rule first.

---

{golden_block}

---

## Test-plan conventions

{conventions_md.strip()}

---

## Cluster catalogue

{clusters_md.strip()}

---

## Writing-plan rules (file naming, scenario IDs, sub-clusters)

{writing_md.strip()}

---

{_MODE_BLOCKS[issue.mode]}

---

## The issue

**Behaviour:**

{issue.behaviour.strip()}

**Reproducer:**

{reproducer_block}

**Cluster hint:** {hint_block}

**Files involved:**

{files_block}

---

## Cluster routing (apply in order, first match wins)

1. Hint names an **existing cluster letter** (e.g. `7g`) → use it. If that letter is taken, advance to the next free letter per `test-writing-plan.md`.
2. Hint names just a **cluster number** (e.g. `7`) → allocate the next free sub-cluster letter inside it.
3. Hint is **`new`** → create a new cluster. Pick the next free cluster number, add a `test-clusters.md` entry, register in `.github/scripts/showcase_clusters.py`, update default selectors in `pip_publish.yml` + `showcase_tests.yml`.
4. Hint is **`others`** or **blank** AND no existing cluster fits → place under `lex/test_project/tests/others/` (create if missing) with generic numbering.

State your placement decision in the PR description: *"Placed under cluster Nx because …"* (one sentence).

---

## Required deliverables in the PR

- New test file under `lex/test_project/tests/<cluster>/test_<Nx>_<slug>.py`.
- `lex/test_project/test-plan/test-clusters.md` updated (status / scenario range for the touched (sub-)cluster).
- One new row appended to the bottom of `lex/test_project/test-plan/progress/session-log.md`.
- Mode B/C only: one new BUG-NNN row in `lex/test_project/test-plan/known-bugs.md`.
- New-cluster placement only: register the new cluster in `.github/scripts/showcase_clusters.py` and update default selectors in `pip_publish.yml` + `showcase_tests.yml`.
- Touch nothing outside the allowed file set, except the source fix in mode `fix-and-test`.

End the PR body with the exact line: `Fixes #{issue.number}` so the gate workflow can find this issue.
"""


def _cli() -> None:
    parser = argparse.ArgumentParser(description="Assemble the Copilot-task issue body.")
    parser.add_argument("--issue-json", required=True, help="Path to JSON file with issue inputs.")
    parser.add_argument("--test-plan-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    raw = json.loads(Path(args.issue_json).read_text())
    issue = IssueInput(
        number=int(raw["number"]),
        title=str(raw["title"]),
        mode=Mode(raw["mode"]),
        behaviour=str(raw.get("behaviour", "")),
        reproducer=str(raw.get("reproducer", "")),
        cluster_hint=str(raw.get("cluster_hint", "")),
        files=list(raw.get("files", []) or []),
    )
    args.out.write_text(assemble_prompt(issue, test_plan_dir=args.test_plan_dir))
    print(f"Wrote {args.out}", file=sys.stderr)


if __name__ == "__main__":
    _cli()
```

- [ ] **Step 2: Run the tests to verify they pass**

Run: `pytest .github/scripts/tests/test_copilot_assemble_prompt.py -v`
Expected: 8 passed.

- [ ] **Step 3: Write the entry workflow**

Write `.github/workflows/copilot_test_bot.yml`:

```yaml
# ───────────────────────────────────────────────────────────────────
#  Copilot Test-Bot — entry point.
#
#  Trigger: a `copilot:<mode>` label is added to an issue, OR an
#  operator runs the workflow manually with an issue number + mode.
#
#  Behaviour: validate the issue, assemble a prompt from the
#  test-plan docs at runtime, file a Copilot-task issue, assign it
#  to copilot-swe-agent[bot]. Ends after the assignment — Copilot
#  takes over from there. The PR-gate workflow handles its PR.
# ───────────────────────────────────────────────────────────────────

name: Copilot Test-Bot

on:
  issues:
    types: [labeled]
  workflow_dispatch:
    inputs:
      issue_number:
        description: "Existing issue number to (re-)dispatch"
        required: false
        type: string
      mode:
        description: "Mode (only used if issue_number is blank)"
        required: false
        type: choice
        options: [regression, bug-repro, fix-and-test]
      publish:
        description: "Set publish-on-merge label"
        required: false
        type: boolean
        default: false

permissions:
  issues: write
  contents: read

jobs:
  dispatch:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    # Only run on label events when the label is one of our three modes.
    if: >-
      github.event_name == 'workflow_dispatch' ||
      github.event.label.name == 'copilot:regression' ||
      github.event.label.name == 'copilot:bug-repro' ||
      github.event.label.name == 'copilot:fix-and-test'

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Resolve issue number + mode
        id: ctx
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          set -euo pipefail
          if [[ "${{ github.event_name }}" == "issues" ]]; then
            ISSUE_NUMBER="${{ github.event.issue.number }}"
            LABEL="${{ github.event.label.name }}"
            MODE="${LABEL#copilot:}"
          else
            ISSUE_NUMBER="${{ inputs.issue_number }}"
            MODE="${{ inputs.mode }}"
            if [[ -z "$ISSUE_NUMBER" || -z "$MODE" ]]; then
              echo "::error::workflow_dispatch requires both issue_number and mode."
              exit 1
            fi
          fi
          echo "issue_number=${ISSUE_NUMBER}" >> "$GITHUB_OUTPUT"
          echo "mode=${MODE}"                 >> "$GITHUB_OUTPUT"

      - name: Validate issue + extract fields
        id: extract
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          ISSUE_NUMBER: ${{ steps.ctx.outputs.issue_number }}
          MODE: ${{ steps.ctx.outputs.mode }}
        run: |
          set -euo pipefail
          ISSUE_JSON=$(gh issue view "$ISSUE_NUMBER" --repo "$GITHUB_REPOSITORY" --json title,body,labels)
          TITLE=$(echo "$ISSUE_JSON" | jq -r .title)
          BODY=$(echo  "$ISSUE_JSON" | jq -r .body)

          # Reject if more than one mode label is present.
          MODE_LABELS=$(echo "$ISSUE_JSON" | jq -r '[.labels[].name | select(startswith("copilot:") and . != "copilot:invalid")] | length')
          if [[ "$MODE_LABELS" -gt 1 ]]; then
            gh issue comment "$ISSUE_NUMBER" --repo "$GITHUB_REPOSITORY" \
              --body "Multiple mode labels found ($MODE_LABELS). Apply exactly one of copilot:regression / copilot:bug-repro / copilot:fix-and-test."
            gh issue edit "$ISSUE_NUMBER" --repo "$GITHUB_REPOSITORY" --add-label "copilot:invalid"
            exit 1
          fi

          # Pull form fields out of the issue body. The issue template emits
          # `### Heading\n\n<value>` blocks; we extract by heading.
          extract_section() {
            python3 - "$1" <<'PY'
          import re, sys, os
          body = os.environ["BODY"]
          heading = sys.argv[1]
          pat = re.compile(rf"^### {re.escape(heading)}\s*\n+(.*?)(?=^### |\Z)", re.MULTILINE | re.DOTALL)
          m = pat.search(body)
          print((m.group(1).strip() if m else "").strip())
          PY
          }
          export BODY
          BEHAVIOUR=$(extract_section "Behaviour description")
          REPRODUCER=$(extract_section "Reproducer / steps")
          CLUSTER_HINT=$(extract_section "Cluster hint (optional)")
          FILES_RAW=$(extract_section "Files involved (optional)")

          if [[ -z "$BEHAVIOUR" ]]; then
            gh issue comment "$ISSUE_NUMBER" --repo "$GITHUB_REPOSITORY" \
              --body "Behaviour description is empty. Edit the issue and re-add the mode label to retry."
            gh issue edit "$ISSUE_NUMBER" --repo "$GITHUB_REPOSITORY" --add-label "copilot:invalid"
            exit 1
          fi

          if [[ "$MODE" != "regression" && -z "$REPRODUCER" ]]; then
            gh issue comment "$ISSUE_NUMBER" --repo "$GITHUB_REPOSITORY" \
              --body "Reproducer is required for mode '$MODE'. Edit the issue and re-add the mode label to retry."
            gh issue edit "$ISSUE_NUMBER" --repo "$GITHUB_REPOSITORY" --add-label "copilot:invalid"
            exit 1
          fi

          # Build files JSON array from CSV-or-newlines input.
          FILES_JSON=$(printf '%s\n' "$FILES_RAW" | python3 -c '
          import json, sys
          raw = sys.stdin.read().strip()
          parts = [p.strip() for chunk in raw.splitlines() for p in chunk.split(",")]
          print(json.dumps([p for p in parts if p]))')

          jq -n \
            --argjson number "$ISSUE_NUMBER" \
            --arg title "$TITLE" \
            --arg mode "$MODE" \
            --arg behaviour "$BEHAVIOUR" \
            --arg reproducer "$REPRODUCER" \
            --arg cluster_hint "$CLUSTER_HINT" \
            --argjson files "$FILES_JSON" \
            '{number: $number, title: $title, mode: $mode, behaviour: $behaviour, reproducer: $reproducer, cluster_hint: $cluster_hint, files: $files}' \
            > issue.json

      - name: Assemble prompt
        run: |
          python3 .github/scripts/copilot_assemble_prompt.py \
            --issue-json issue.json \
            --test-plan-dir lex/test_project/test-plan \
            --out prompt.md

      - name: Create Copilot-task issue + assign Copilot
        env:
          GH_TOKEN: ${{ secrets.COPILOT_PAT }}
          PARENT_ISSUE: ${{ steps.ctx.outputs.issue_number }}
        run: |
          set -euo pipefail
          TITLE_JSON=$(jq -r .title issue.json)
          TASK_URL=$(gh issue create \
            --repo "$GITHUB_REPOSITORY" \
            --title "[copilot-task] $TITLE_JSON" \
            --body-file prompt.md \
            --assignee copilot-swe-agent)
          gh issue comment "$PARENT_ISSUE" --repo "$GITHUB_REPOSITORY" \
            --body "Copilot task dispatched: $TASK_URL"
```

- [ ] **Step 4: Commit**

```bash
git add .github/scripts/__init__.py .github/scripts/tests/__init__.py \
        .github/scripts/copilot_assemble_prompt.py \
        .github/scripts/tests/test_copilot_assemble_prompt.py \
        .github/workflows/copilot_test_bot.yml
git commit -m "feat(ci): copilot_test_bot.yml + copilot_assemble_prompt.py with pytest coverage"
```

(The `.github/scripts/__init__.py` line above is harmless if the directory already has one; if not, create it as an empty file before committing — `.github/scripts/` is treated as a flat script directory by the repo, but `tests/` needs its own marker for pytest to discover it.)

---

## Task 9: `copilot_validate_pr_shape.py` — write failing pytest first

**Files:**
- Create: `.github/scripts/tests/test_copilot_validate_pr_shape.py`

The PR-gate check matrix (spec §7) is the highest-leverage code in this design — every Copilot PR runs through it. TDD pins the per-mode contract before the script lands.

- [ ] **Step 1: Write the failing tests**

Write `.github/scripts/tests/test_copilot_validate_pr_shape.py`:

```python
"""Tests for copilot_validate_pr_shape — per-mode PR-shape gating."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from copilot_validate_pr_shape import (  # noqa: E402
    PRFile,
    ValidationResult,
    validate_pr_shape,
)


def _files(*pairs: tuple[str, int]) -> list[PRFile]:
    """Build PRFile list from (path, additions) tuples. Deletions default to 0."""
    return [PRFile(path=p, additions=a, deletions=0) for p, a in pairs]


# ----- happy paths -------------------------------------------------

def test_regression_happy_path_ok() -> None:
    files = _files(
        ("lex/test_project/tests/cluster_7/test_7m_thing.py", 80),
        ("lex/test_project/test-plan/test-clusters.md", 3),
        ("lex/test_project/test-plan/progress/session-log.md", 1),
    )
    pr_body = "Closes the gap on scenario 7.NN.\n\nFixes #42"
    result = validate_pr_shape(mode="regression", files=files, pr_body=pr_body)
    assert isinstance(result, ValidationResult)
    assert result.ok, result.errors


def test_bug_repro_happy_path_ok() -> None:
    files = _files(
        ("lex/test_project/tests/cluster_5/test_5m_thing.py", 60),
        ("lex/test_project/test-plan/test-clusters.md", 2),
        ("lex/test_project/test-plan/progress/session-log.md", 1),
        ("lex/test_project/test-plan/known-bugs.md", 1),
    )
    pr_body = "Reproduces BUG-099.\n\nFixes #43"
    result = validate_pr_shape(mode="bug-repro", files=files, pr_body=pr_body)
    assert result.ok, result.errors


def test_fix_and_test_happy_path_ok() -> None:
    files = _files(
        ("lex/test_project/tests/cluster_5/test_5n_thing.py", 50),
        ("lex/test_project/test-plan/test-clusters.md", 2),
        ("lex/test_project/test-plan/progress/session-log.md", 1),
        ("lex/test_project/test-plan/known-bugs.md", 1),
        ("lex/core/services/foo.py", 12),  # source fix, under 50 lines
    )
    pr_body = (
        "Fixes BUG-100.\n\n"
        "### Source changes\n- `lex/core/services/foo.py`: short rationale.\n\n"
        "Fixes #44"
    )
    result = validate_pr_shape(mode="fix-and-test", files=files, pr_body=pr_body)
    assert result.ok, result.errors


# ----- shape failures ---------------------------------------------

def test_missing_test_file_fails() -> None:
    files = _files(
        ("lex/test_project/test-plan/test-clusters.md", 3),
        ("lex/test_project/test-plan/progress/session-log.md", 1),
    )
    result = validate_pr_shape(mode="regression", files=files, pr_body="Fixes #1")
    assert not result.ok
    assert any("no new test file" in e.lower() for e in result.errors)


def test_missing_session_log_append_fails() -> None:
    files = _files(
        ("lex/test_project/tests/cluster_7/test_7m_thing.py", 60),
        ("lex/test_project/test-plan/test-clusters.md", 3),
    )
    result = validate_pr_shape(mode="regression", files=files, pr_body="Fixes #1")
    assert not result.ok
    assert any("session-log.md" in e for e in result.errors)


def test_missing_test_clusters_modification_fails() -> None:
    """All three modes require a `test-plan/test-clusters.md` modification per §7."""
    files = _files(
        ("lex/test_project/tests/cluster_7/test_7m_thing.py", 60),
        ("lex/test_project/test-plan/progress/session-log.md", 1),
    )
    result = validate_pr_shape(mode="regression", files=files, pr_body="Fixes #1")
    assert not result.ok
    assert any("test-clusters.md" in e for e in result.errors)


def test_missing_known_bugs_row_fails_bug_repro() -> None:
    files = _files(
        ("lex/test_project/tests/cluster_5/test_5m_thing.py", 60),
        ("lex/test_project/test-plan/test-clusters.md", 2),
        ("lex/test_project/test-plan/progress/session-log.md", 1),
    )
    result = validate_pr_shape(mode="bug-repro", files=files, pr_body="Fixes #1")
    assert not result.ok
    assert any("known-bugs.md" in e for e in result.errors)


def test_test_filename_regex_violation_fails() -> None:
    files = _files(
        ("lex/test_project/tests/cluster_7/test_thing_no_scenario_id.py", 60),
        ("lex/test_project/test-plan/test-clusters.md", 3),
        ("lex/test_project/test-plan/progress/session-log.md", 1),
    )
    result = validate_pr_shape(mode="regression", files=files, pr_body="Fixes #1")
    assert not result.ok
    assert any("test_<Nx>_" in e or "naming" in e.lower() for e in result.errors)


def test_files_outside_allowed_set_fail_for_test_only_modes() -> None:
    """A regression-mode PR may not touch source."""
    files = _files(
        ("lex/test_project/tests/cluster_7/test_7m_thing.py", 60),
        ("lex/test_project/test-plan/test-clusters.md", 3),
        ("lex/test_project/test-plan/progress/session-log.md", 1),
        ("lex/core/services/foo.py", 5),
    )
    result = validate_pr_shape(mode="regression", files=files, pr_body="Fixes #1")
    assert not result.ok
    assert any("lex/core/services/foo.py" in e for e in result.errors)


def test_fix_and_test_source_diff_above_50_lines_fails() -> None:
    files = _files(
        ("lex/test_project/tests/cluster_5/test_5n_thing.py", 50),
        ("lex/test_project/test-plan/test-clusters.md", 2),
        ("lex/test_project/test-plan/progress/session-log.md", 1),
        ("lex/test_project/test-plan/known-bugs.md", 1),
        ("lex/core/services/foo.py", 30),
        ("lex/core/services/bar.py", 25),
    )
    pr_body = (
        "### Source changes\n"
        "- `lex/core/services/foo.py`: a\n"
        "- `lex/core/services/bar.py`: b\n"
        "\nFixes #99"
    )
    result = validate_pr_shape(mode="fix-and-test", files=files, pr_body=pr_body)
    assert not result.ok
    assert any("source diff" in e.lower() for e in result.errors)


def test_fix_and_test_source_file_not_listed_in_body_fails() -> None:
    files = _files(
        ("lex/test_project/tests/cluster_5/test_5n_thing.py", 50),
        ("lex/test_project/test-plan/test-clusters.md", 2),
        ("lex/test_project/test-plan/progress/session-log.md", 1),
        ("lex/test_project/test-plan/known-bugs.md", 1),
        ("lex/core/services/foo.py", 12),
    )
    pr_body = "no source changes section\n\nFixes #1"
    result = validate_pr_shape(mode="fix-and-test", files=files, pr_body=pr_body)
    assert not result.ok
    assert any("source changes" in e.lower() for e in result.errors)


def test_fix_and_test_source_path_mismatched_in_body_fails() -> None:
    """The `### Source changes` section lists a different path than the actual diff."""
    files = _files(
        ("lex/test_project/tests/cluster_5/test_5n_thing.py", 50),
        ("lex/test_project/test-plan/test-clusters.md", 2),
        ("lex/test_project/test-plan/progress/session-log.md", 1),
        ("lex/test_project/test-plan/known-bugs.md", 1),
        ("lex/core/services/foo.py", 12),
    )
    pr_body = (
        "Fixes BUG-100.\n\n"
        "### Source changes\n- `lex/core/services/bar.py`: wrong path.\n\n"
        "Fixes #44"
    )
    result = validate_pr_shape(mode="fix-and-test", files=files, pr_body=pr_body)
    assert not result.ok
    assert any("lex/core/services/foo.py" in e for e in result.errors)


def test_missing_fixes_link_fails() -> None:
    files = _files(
        ("lex/test_project/tests/cluster_7/test_7m_thing.py", 60),
        ("lex/test_project/test-plan/test-clusters.md", 3),
        ("lex/test_project/test-plan/progress/session-log.md", 1),
    )
    result = validate_pr_shape(
        mode="regression", files=files, pr_body="No issue link here."
    )
    assert not result.ok
    assert any("Fixes #" in e for e in result.errors)


def test_missing_fixes_link_fails_bug_repro() -> None:
    """Check 1 of §7 fails any mode with no issue link — pin that bug-repro is not special-cased."""
    files = _files(
        ("lex/test_project/tests/cluster_5/test_5m_thing.py", 60),
        ("lex/test_project/test-plan/test-clusters.md", 2),
        ("lex/test_project/test-plan/progress/session-log.md", 1),
        ("lex/test_project/test-plan/known-bugs.md", 1),
    )
    result = validate_pr_shape(
        mode="bug-repro", files=files, pr_body="No issue link here."
    )
    assert not result.ok
    assert any("Fixes #" in e for e in result.errors)


def test_unknown_mode_raises() -> None:
    with pytest.raises(ValueError):
        validate_pr_shape(mode="banana", files=[], pr_body="")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest .github/scripts/tests/test_copilot_validate_pr_shape.py -v`
Expected: `ImportError: cannot import name 'PRFile' ...` — TDD red phase confirmed.

---

## Task 10: `copilot_validate_pr_shape.py` — implementation + PR-gate workflow

**Files:**
- Create: `.github/scripts/copilot_validate_pr_shape.py`
- Create: `.github/workflows/copilot_pr_gate.yml`

- [ ] **Step 1: Implement the script**

Write `.github/scripts/copilot_validate_pr_shape.py`:

```python
"""Validate the shape of a Copilot test-bot PR (file set, naming, body markers).

Used by ``copilot_pr_gate.yml`` to apply the per-mode contract from
``docs/superpowers/specs/2026-05-13-copilot-test-bot-design.md`` §7.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

VALID_MODES = ("regression", "bug-repro", "fix-and-test")

# File-set rules ------------------------------------------------------

TEST_FILE_RE = re.compile(
    r"^lex/test_project/tests/(?P<cluster>[A-Za-z0-9_]+)/test_(?P<scenario>\d+[a-z]?)_[A-Za-z0-9_]+\.py$"
)

ALLOWED_TEST_ONLY_PREFIXES = (
    "lex/test_project/",
    ".github/workflows/",
    ".github/scripts/showcase_clusters.py",
)

MAX_FIX_AND_TEST_SOURCE_LINES = 50
SOURCE_CHANGES_HEADING = "### Source changes"
FIXES_LINK_RE = re.compile(r"(?im)^Fixes\s+#\d+\s*$")


@dataclass
class PRFile:
    path: str
    additions: int
    deletions: int

    @property
    def delta(self) -> int:
        return self.additions + self.deletions


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)


def _is_test_file(path: str) -> bool:
    return bool(TEST_FILE_RE.match(path))


def _is_allowed_for_test_only(path: str) -> bool:
    return any(path == p or path.startswith(p) for p in ALLOWED_TEST_ONLY_PREFIXES)


def validate_pr_shape(*, mode: str, files: list[PRFile], pr_body: str) -> ValidationResult:
    if mode not in VALID_MODES:
        raise ValueError(f"unknown mode {mode!r}; valid: {VALID_MODES}")

    errors: list[str] = []
    paths = [f.path for f in files]

    # 1. New test file present + name matches Nx_slug pattern.
    test_files = [p for p in paths if _is_test_file(p)]
    if not test_files:
        # Distinguish "no test file at all" from "test file with bad name".
        bad_named = [
            p for p in paths
            if p.startswith("lex/test_project/tests/") and p.endswith(".py")
            and not _is_test_file(p)
        ]
        if bad_named:
            errors.append(
                "Test file naming violates `test_<Nx>_<slug>.py`: "
                + ", ".join(bad_named)
            )
        else:
            errors.append("no new test file under `lex/test_project/tests/<cluster>/`")

    # 2. test-clusters.md modified.
    if "lex/test_project/test-plan/test-clusters.md" not in paths:
        errors.append("`lex/test_project/test-plan/test-clusters.md` was not modified")

    # 3. Session log appended.
    if "lex/test_project/test-plan/progress/session-log.md" not in paths:
        errors.append("`lex/test_project/test-plan/progress/session-log.md` was not appended")

    # 4. Mode-B / Mode-C: known-bugs.md row added.
    if mode in ("bug-repro", "fix-and-test"):
        if "lex/test_project/test-plan/known-bugs.md" not in paths:
            errors.append(
                "`lex/test_project/test-plan/known-bugs.md` was not modified — "
                "modes bug-repro and fix-and-test require a new BUG-NNN row"
            )

    # 5. Source-file rules.
    test_only = mode in ("regression", "bug-repro")
    forbidden_source: list[str] = []
    source_files: list[PRFile] = []
    for f in files:
        if _is_test_file(f.path):
            continue
        if f.path.startswith("lex/test_project/test-plan/"):
            continue
        if _is_allowed_for_test_only(f.path):
            continue
        # Anything else is source.
        if test_only:
            forbidden_source.append(f.path)
        else:
            source_files.append(f)

    if forbidden_source:
        errors.append(
            "PR touches files outside the allowed set for mode "
            f"{mode}: {', '.join(sorted(forbidden_source))}"
        )

    # 6. fix-and-test: source-diff cap + body listing.
    if mode == "fix-and-test":
        total_source_delta = sum(f.delta for f in source_files)
        if total_source_delta > MAX_FIX_AND_TEST_SOURCE_LINES:
            errors.append(
                "source diff is "
                f"{total_source_delta} lines (cap {MAX_FIX_AND_TEST_SOURCE_LINES})"
            )
        if SOURCE_CHANGES_HEADING not in pr_body:
            errors.append(
                f"PR body must include a `{SOURCE_CHANGES_HEADING}` section listing every "
                "source file touched"
            )
        else:
            tail = pr_body.split(SOURCE_CHANGES_HEADING, 1)[1]
            for f in source_files:
                if f.path not in tail:
                    errors.append(
                        f"source file `{f.path}` not listed under `{SOURCE_CHANGES_HEADING}`"
                    )

    # 7. PR body links the originating issue.
    if not FIXES_LINK_RE.search(pr_body or ""):
        errors.append("PR body must contain `Fixes #N` (the originating issue)")

    return ValidationResult(ok=not errors, errors=errors)


def _cli() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True)
    parser.add_argument("--files-json", required=True, type=Path,
                        help='JSON: [{"path":..., "additions":N, "deletions":N}, ...]')
    parser.add_argument("--body-file", required=True, type=Path)
    args = parser.parse_args()

    raw = json.loads(args.files_json.read_text())
    files = [
        PRFile(path=r["path"], additions=int(r.get("additions", 0)), deletions=int(r.get("deletions", 0)))
        for r in raw
    ]
    body = args.body_file.read_text()
    result = validate_pr_shape(mode=args.mode, files=files, pr_body=body)
    if result.ok:
        print("OK")
        return 0
    for e in result.errors:
        print(f"- {e}")
    return 1


if __name__ == "__main__":
    sys.exit(_cli())
```

- [ ] **Step 2: Run the tests to verify they pass**

Run: `pytest .github/scripts/tests/test_copilot_validate_pr_shape.py -v`
Expected: 15 passed.

- [ ] **Step 3: Write the PR-gate workflow**

Write `.github/workflows/copilot_pr_gate.yml`:

```yaml
# ───────────────────────────────────────────────────────────────────
#  Copilot Test-Bot — PR gate.
#
#  Runs on every PR authored by copilot-swe-agent[bot]. Does:
#    1. Find the originating issue via "Fixes #N" in the PR body,
#       read its mode label.
#    2. Static PR-shape check via copilot_validate_pr_shape.py.
#    3. Run the new test in isolation. Mode-B strips
#       @expectedFailure on a temp copy and asserts FAILURE.
#    4. Apply auto-merge (modes A/B) or needs-human-review (mode C).
#
#  Any failure → label PR copilot:invalid + comment + exit non-zero
#  (the comment lists what failed; merge labels are not applied).
# ───────────────────────────────────────────────────────────────────

name: Copilot PR Gate

on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review]

permissions:
  contents: read
  pull-requests: write
  issues: read

concurrency:
  # Coalesce rapid synchronize events on the same PR — the gate
  # mutates labels, posts comments, and (mode A/B) calls --auto;
  # racing runs would double-comment / double-label.
  group: copilot-pr-gate-${{ github.event.pull_request.number }}
  cancel-in-progress: true

jobs:
  gate:
    if: github.event.pull_request.user.login == 'copilot-swe-agent[bot]'
    runs-on: ubuntu-latest
    timeout-minutes: 20

    services:
      # Required: the Django test runner needs a real Postgres for
      # migrate + per-test transactions. Mirrors showcase_tests.yml so
      # cluster fixtures behave identically here vs. the full suite.
      postgres:
        image: postgres:latest
        env:
          POSTGRES_USER: django
          POSTGRES_PASSWORD: lundadminlocal
          POSTGRES_DB: db_lex
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 3

    steps:
      - name: Checkout PR head
        uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.head.sha }}
          fetch-depth: 0

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          # Match showcase_tests.yml so the gate runs against the same
          # interpreter the project itself targets.
          python-version: "3.12.0"

      - name: Install lex-app
        # `pip install -e .` puts the project's `lex` console-script on
        # PATH — Mode-A/B/C invoke `lex test ...` (Django manage entry).
        # Without this, `lex` is "command not found" on the GHA runner.
        run: |
          pip install --upgrade pip setuptools wheel
          pip install --prefer-binary -r requirements.txt
          pip install -e .

      # ── 1. Discover mode from the linked issue ─────────────────
      - name: Discover mode
        id: mode
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          PR_BODY: ${{ github.event.pull_request.body }}
        run: |
          set -euo pipefail
          ISSUE_NUM=$(printf '%s' "$PR_BODY" | grep -oE 'Fixes #[0-9]+' | head -n1 | sed 's/Fixes #//')
          if [[ -z "$ISSUE_NUM" ]]; then
            echo "::error::PR body has no 'Fixes #N' link."
            exit 1
          fi
          LABELS=$(gh issue view "$ISSUE_NUM" --repo "$GITHUB_REPOSITORY" --json labels --jq '.labels[].name')
          MODE=""
          for L in $LABELS; do
            case "$L" in
              copilot:regression)    MODE="regression" ;;
              copilot:bug-repro)     MODE="bug-repro" ;;
              copilot:fix-and-test)  MODE="fix-and-test" ;;
            esac
          done
          if [[ -z "$MODE" ]]; then
            echo "::error::Linked issue #$ISSUE_NUM has no copilot:<mode> label."
            exit 1
          fi
          echo "mode=${MODE}"           >> "$GITHUB_OUTPUT"
          echo "issue=${ISSUE_NUM}"     >> "$GITHUB_OUTPUT"

      # ── 2. PR-shape gate ───────────────────────────────────────
      - name: Build files JSON + body file
        id: files
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          PR_NUMBER: ${{ github.event.pull_request.number }}
        run: |
          set -euo pipefail
          # Use the REST API (paginated) so we get per-file `status`
          # (added|modified|removed|renamed). `gh pr view --json files`
          # does not surface status, which the test-file locator needs
          # to filter to additions-only — otherwise a `synchronize`
          # event editing a pre-existing test file would be picked.
          gh api --paginate "repos/$GITHUB_REPOSITORY/pulls/$PR_NUMBER/files" \
            --jq '[.[] | {path: .filename, additions: .additions, deletions: .deletions, status: .status}]' \
            > files.json
          gh pr view "$PR_NUMBER" --repo "$GITHUB_REPOSITORY" --json body --jq .body > body.txt

      - name: Run shape validator
        id: shape
        run: |
          python .github/scripts/copilot_validate_pr_shape.py \
            --mode "${{ steps.mode.outputs.mode }}" \
            --files-json files.json \
            --body-file body.txt | tee shape.out

      - name: On shape failure, label + comment
        if: failure()
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          PR_NUMBER: ${{ github.event.pull_request.number }}
        run: |
          BODY="PR-shape check failed:\n\n$(cat shape.out)"
          gh pr comment "$PR_NUMBER" --repo "$GITHUB_REPOSITORY" --body "$BODY"
          gh pr edit    "$PR_NUMBER" --repo "$GITHUB_REPOSITORY" --add-label "copilot:invalid"
          exit 1

      # ── 3. Run the new test (mode A/C: must pass; mode B: must fail without decorator) ──
      - name: Locate new test file
        id: testfile
        run: |
          set -euo pipefail
          # Filter to status=='added' — on a `synchronize` event the
          # PR may also touch an existing test file that matches the
          # naming regex; without this filter `head -n1` would pick the
          # wrong one and Mode-A/B/C would run the wrong scenario.
          NEW_TEST=$(jq -r '.[] | select(.status == "added") | .path | select(test("^lex/test_project/tests/[^/]+/test_[0-9]+[a-z]?_[A-Za-z0-9_]+\\.py$"))' files.json | head -n1)
          if [[ -z "$NEW_TEST" ]]; then
            echo "::error::could not locate the new test file (no added file matching test_<Nx>_<slug>.py under lex/test_project/tests/<cluster>/)."
            exit 1
          fi
          echo "path=${NEW_TEST}" >> "$GITHUB_OUTPUT"

      - name: Mode A or C — run test, expect pass
        if: steps.mode.outputs.mode != 'bug-repro'
        env:
          # Mirror showcase_tests.yml so the gate exercises the new
          # test under the same Django settings as the full cluster
          # suite — a bug-repro that only fires under different
          # settings would otherwise slip through.
          DJANGO_SETTINGS_MODULE: lex_app.settings
          DATABASE_DEPLOYMENT_TARGET: default
          CELERY_ACTIVE: "False"
        run: |
          set -euo pipefail
          # `lex` is the Django manage.py console-script installed by
          # `pip install -e .` in the install step.
          TEST_PATH="${{ steps.testfile.outputs.path }}"
          MODULE=$(echo "${TEST_PATH%.py}" | tr '/' '.')
          lex test "$MODULE" --noinput

      - name: Mode B — strip @expectedFailure, expect FAIL
        if: steps.mode.outputs.mode == 'bug-repro'
        env:
          DJANGO_SETTINGS_MODULE: lex_app.settings
          DATABASE_DEPLOYMENT_TARGET: default
          CELERY_ACTIVE: "False"
        run: |
          set -euo pipefail
          TEST_PATH="${{ steps.testfile.outputs.path }}"
          # Strip the @(unittest.)?expectedFailure decorator IN PLACE.
          # The CI checkout is throwaway; overwriting keeps the file at
          # its real package path so the Django test runner discovers
          # it as part of `lex/test_project/tests/<cluster>/`. A temp
          # file outside the package would not be importable as
          # `lex.test_project.tests.cluster_X.test_Nx_thing`.
          python - "$TEST_PATH" <<'PY'
          import re, sys, pathlib
          p = pathlib.Path(sys.argv[1])
          src = p.read_text()
          out = re.sub(r"(?m)^\s*@(?:unittest\.)?expectedFailure\s*\n", "", src)
          if out == src:
              print("::error::no @expectedFailure decorator found to strip — Mode-B requires one on the new test")
              sys.exit(2)
          p.write_text(out)
          PY
          MODULE=$(echo "${TEST_PATH%.py}" | tr '/' '.')
          # Run via the Django test runner (NOT pytest — the project's
          # tests are unittest.TestCase under Django settings; pytest
          # without pytest-django would either not collect or crash on
          # django.setup(), which would look like 'correctly fails'
          # without actually proving the bug reproduces).
          set +e
          lex test "$MODULE" --noinput 2>&1 | tee mode_b.out
          EXIT=${PIPESTATUS[0]}
          set -e
          if [[ $EXIT -eq 0 ]]; then
            echo "::error::Mode-B test passed without @expectedFailure — bug is not reproducible."
            exit 1
          fi
          # Distinguish 'ran and asserted FAIL' from 'errored on
          # import/setup'. Django's unittest runner prints lines
          # starting with `FAIL:` (assertion) or `FAILED` (summary).
          # Anything else is a non-test failure that we MUST not treat
          # as a successful bug repro.
          if grep -qE '^(FAIL|FAILED)' mode_b.out; then
            echo "Mode-B test correctly fails without the decorator."
          else
            echo "::error::Mode-B test errored before assertion (likely import/setup failure) — not a real bug repro."
            exit 1
          fi

      # ── 4. Apply merge labels ──────────────────────────────────
      - name: Apply auto-merge (modes A/B)
        if: steps.mode.outputs.mode != 'fix-and-test'
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          PR_NUMBER: ${{ github.event.pull_request.number }}
        run: |
          gh pr edit  "$PR_NUMBER" --repo "$GITHUB_REPOSITORY" --add-label "auto-merge"
          gh pr merge "$PR_NUMBER" --repo "$GITHUB_REPOSITORY" --squash --auto

      - name: Route mode-C to human review
        if: steps.mode.outputs.mode == 'fix-and-test'
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          PR_NUMBER: ${{ github.event.pull_request.number }}
        run: |
          gh pr edit "$PR_NUMBER" --repo "$GITHUB_REPOSITORY" --add-label "needs-human-review"
          # The team-review request is best-effort — if the team isn't configured, log and continue.
          gh pr edit "$PR_NUMBER" --repo "$GITHUB_REPOSITORY" --add-reviewer "lex-maintainers" || \
            echo "::warning::could not request review from lex-maintainers (team not found?)"
```

- [ ] **Step 4: Commit**

```bash
git add .github/scripts/copilot_validate_pr_shape.py \
        .github/scripts/tests/test_copilot_validate_pr_shape.py \
        .github/workflows/copilot_pr_gate.yml
git commit -m "feat(ci): copilot_pr_gate.yml + copilot_validate_pr_shape.py with per-mode contract tests"
```

---

## Task 11: `copilot_compute_next_rc.py` — write failing pytest first

**Files:**
- Create: `.github/scripts/tests/test_copilot_compute_next_rc.py`

This is the smallest helper — but it sits in front of the draft-release call, so a regression here would mis-version every release. TDD covers every shape the existing tag space might emit.

- [ ] **Step 1: Write the failing tests**

Write `.github/scripts/tests/test_copilot_compute_next_rc.py`:

```python
"""Tests for copilot_compute_next_rc.compute_next_rc."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from copilot_compute_next_rc import compute_next_rc  # noqa: E402


def test_basic_rc_bump() -> None:
    assert compute_next_rc("v2.0.0rc124") == "v2.0.0rc125"


def test_two_digit_rc_bump() -> None:
    assert compute_next_rc("v2.0.0rc99") == "v2.0.0rc100"


def test_three_digit_minor_with_rc() -> None:
    assert compute_next_rc("v10.15.7rc3") == "v10.15.7rc4"


def test_zero_padded_rc_is_normalised() -> None:
    # If a human ever pushed rc007 manually, we still bump cleanly.
    assert compute_next_rc("v2.0.0rc007") == "v2.0.0rc8"


def test_non_rc_tag_rejected() -> None:
    with pytest.raises(ValueError, match=r"(?i)rc"):
        compute_next_rc("v2.0.0")


def test_completely_wrong_shape_rejected() -> None:
    with pytest.raises(ValueError):
        compute_next_rc("release-2.0.0")


def test_empty_string_rejected() -> None:
    with pytest.raises(ValueError):
        compute_next_rc("")


def test_pre_release_other_than_rc_rejected() -> None:
    # The publish path is explicitly rc-only — anything else is a human concern.
    with pytest.raises(ValueError, match=r"(?i)rc"):
        compute_next_rc("v2.0.0a1")
    with pytest.raises(ValueError, match=r"(?i)rc"):
        compute_next_rc("v2.0.0b5")


def test_uppercase_rc_rejected() -> None:
    # Tag space discipline: only lowercase `rc` is allowed. Forbid
    # silent normalisation — uppercase usually means a typo upstream
    # (manual `git tag`), and silently accepting it would hide that.
    with pytest.raises(ValueError):
        compute_next_rc("v2.0.0RC1")


def test_whitespace_input_rejected() -> None:
    # Reject leading/trailing whitespace + newlines outright. A workflow
    # capturing the previous tag from `gh release view` may include a
    # stray newline; silently `.strip()`-ing it would mask that bug. The
    # caller must hand us a clean tag.
    with pytest.raises(ValueError):
        compute_next_rc(" v2.0.0rc1\n")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest .github/scripts/tests/test_copilot_compute_next_rc.py -v`
Expected: ImportError — confirms TDD red phase.

---

## Task 12: `copilot_compute_next_rc.py` — implementation + publish workflow

**Files:**
- Create: `.github/scripts/copilot_compute_next_rc.py`
- Create: `.github/workflows/copilot_publish_after_merge.yml`

- [ ] **Step 1: Implement the script**

Write `.github/scripts/copilot_compute_next_rc.py`:

```python
"""Bump an rc-suffixed tag by one. Refuses anything that isn't already an rc."""

from __future__ import annotations

import argparse
import re
import sys

_RC_RE = re.compile(r"^v(?P<base>\d+\.\d+\.\d+)rc(?P<n>\d+)$")


def compute_next_rc(tag: str) -> str:
    m = _RC_RE.match(tag or "")
    if not m:
        raise ValueError(
            f"not an rc tag: {tag!r} — expected vX.Y.ZrcN (e.g. v2.0.0rc124)"
        )
    next_n = int(m.group("n")) + 1
    return f"v{m.group('base')}rc{next_n}"


def _cli() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--current", required=True, help="The latest existing rc tag.")
    args = parser.parse_args()
    print(compute_next_rc(args.current))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
```

- [ ] **Step 2: Run the tests to verify they pass**

Run: `pytest .github/scripts/tests/test_copilot_compute_next_rc.py -v`
Expected: 10 passed.

- [ ] **Step 3: Write the publish workflow**

Write `.github/workflows/copilot_publish_after_merge.yml`:

```yaml
# ───────────────────────────────────────────────────────────────────
#  Copilot Test-Bot — optional post-merge publish.
#
#  Fires when a Copilot PR with both `auto-merge` and
#  `publish-on-merge` labels gets merged. Bumps the rc suffix on
#  the latest release tag and creates a DRAFT GitHub release; the
#  existing `pip_publish.yml` (release: created) takes over from
#  there to publish to PyPI.
#
#  Default off — gated by repo variable COPILOT_AUTO_PUBLISH_ENABLED.
#  Set to "true" only after one full A-mode round-trip has been
#  observed end-to-end.
# ───────────────────────────────────────────────────────────────────

name: Copilot Publish After Merge

on:
  pull_request:
    types: [closed]

permissions:
  contents: write
  pull-requests: read

# Serialize runs so two PRs merged seconds apart don't both compute
# the same nextRc and race on `gh release create`.
concurrency:
  group: copilot-publish
  cancel-in-progress: false

jobs:
  draft-release:
    if: >-
      github.event.pull_request.merged == true &&
      github.event.pull_request.user.login == 'copilot-swe-agent[bot]' &&
      contains(github.event.pull_request.labels.*.name, 'auto-merge') &&
      contains(github.event.pull_request.labels.*.name, 'publish-on-merge') &&
      vars.COPILOT_AUTO_PUBLISH_ENABLED == 'true'
    runs-on: ubuntu-latest
    timeout-minutes: 10

    steps:
      - name: Checkout default branch
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Find latest rc tag
        id: latest
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          set -euo pipefail
          # gh release list emits one row per release; the first match
          # for a tag like vX.Y.ZrcN is what we bump from.
          CURRENT=$(gh release list --repo "$GITHUB_REPOSITORY" --limit 50 \
            --json tagName --jq '[.[] | .tagName | select(test("^v[0-9]+\\.[0-9]+\\.[0-9]+rc[0-9]+$"))][0]')
          if [[ -z "$CURRENT" || "$CURRENT" == "null" ]]; then
            echo "::error::no rc tag found in last 50 releases — manual release required."
            exit 1
          fi
          echo "current=${CURRENT}" >> "$GITHUB_OUTPUT"

      - name: Compute next rc
        id: next
        run: |
          NEXT=$(python .github/scripts/copilot_compute_next_rc.py --current "${{ steps.latest.outputs.current }}")
          echo "tag=${NEXT}" >> "$GITHUB_OUTPUT"

      - name: Build release notes
        id: notes
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          PREV: ${{ steps.latest.outputs.current }}
          NEXT: ${{ steps.next.outputs.tag }}
        run: |
          set -euo pipefail
          # GitHub PR search only honors date-only qualifiers
          # (merged:>=YYYY-MM-DD); a full ISO-8601 timestamp like
          # 2026-05-13T10:22:31Z silently matches nothing. Fetch with a
          # date-only lower bound, then post-filter by the precise
          # publishedAt timestamp via jq to drop PRs merged earlier on
          # PREV_DATE before the previous release was published.
          PREV_PUBLISHED=$(gh release view "$PREV" --repo "$GITHUB_REPOSITORY" --json publishedAt --jq .publishedAt)
          # Refuse to compute notes if the previous release has no
          # publishedAt — happens when a maintainer paused the chain by
          # leaving the prior rc as a draft. Same family of silent-empty
          # failure as C1; fail loud instead.
          if [[ -z "$PREV_PUBLISHED" || "$PREV_PUBLISHED" == "null" ]]; then
            echo "::error::previous release '$PREV' has no publishedAt — manual release required."
            exit 1
          fi
          PREV_DATE=${PREV_PUBLISHED%%T*}
          {
            echo "Release ${NEXT} — auto-cut by copilot_publish_after_merge.yml."
            echo
            echo "## PRs since ${PREV}"
            echo
            gh pr list --repo "$GITHUB_REPOSITORY" \
              --state merged --search "merged:>=${PREV_DATE}" --limit 100 \
              --json number,title,author,mergedAt \
              | jq --arg cut "$PREV_PUBLISHED" -r '.[] | select(.mergedAt > $cut) | "- #\(.number) \(.title) (\(.author.login))"'
          } > notes.md

      - name: Create draft release
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          NEXT: ${{ steps.next.outputs.tag }}
        run: |
          gh release create "$NEXT" \
            --repo "$GITHUB_REPOSITORY" \
            --draft \
            --title "$NEXT" \
            --notes-file notes.md
          echo "Draft release $NEXT created — pip_publish.yml takes over."
```

- [ ] **Step 4: Commit**

```bash
git add .github/scripts/copilot_compute_next_rc.py \
        .github/scripts/tests/test_copilot_compute_next_rc.py \
        .github/workflows/copilot_publish_after_merge.yml
git commit -m "feat(ci): copilot_publish_after_merge.yml + copilot_compute_next_rc.py (kill-switch via vars.COPILOT_AUTO_PUBLISH_ENABLED)"
```

---

## Task 13: User-facing doc `docs/ci-cd/copilot-test-bot.md`

**Files:**
- Create: `docs/ci-cd/copilot-test-bot.md`

Mirror the structure of `docs/ci-cd/automated-docs-pipeline.md` so the two CI docs sit next to each other and read consistently.

- [ ] **Step 1: Write the doc**

Write `docs/ci-cd/copilot-test-bot.md`:

```markdown
# Copilot Test-Bot

> **Owner:** CI/CD
> **Repos involved:** `lex-app` (single-repo — no cross-repo dispatch)
> **Last updated:** 2026-05-13

---

## What it does

File an issue describing a behaviour or bug, label it with a `copilot:<mode>` label, and Copilot will:

1. Read the issue + the project's existing test conventions (`lex/test_project/test-plan/`).
2. Write a test that codifies the behaviour, following the Golden Rule (*test what the framework is **trying** to achieve, not what the current code happens to do*).
3. Run the test against current code.
4. Open a PR.
5. **Auto-merge** the PR when CI is green (modes `regression` and `bug-repro`), or **route to human review** (mode `fix-and-test`).
6. Optionally cut a draft `rc` release after merge — which flows into the existing `pip_publish.yml` → PyPI.

---

## Modes

| Mode | Label | What Copilot does | Auto-merge? | Source changes? |
|---|---|---|---|---|
| **regression** | `copilot:regression` | Writes a **passing** test for the described behaviour | yes (green CI) | none |
| **bug-repro** | `copilot:bug-repro` | Writes an `@expectedFailure` test that reproduces a bug; appends a row to `known-bugs.md` | yes (green CI) | none |
| **fix-and-test** | `copilot:fix-and-test` | Writes a failing test, appends a `known-bugs.md` row, then makes the smallest source change that makes it pass | **no — human review** | ≤ 50 lines, listed in PR body |

---

## How to file an issue

1. Open a new issue using the *"Copilot Test Request"* template (`.github/ISSUE_TEMPLATE/copilot-test-request.yml`).
2. Pick a mode in the dropdown — the workflow rejects issues with no mode or two modes.
3. Fill the **Behaviour description** (required) and **Reproducer** (required for `bug-repro`/`fix-and-test`).
4. *(Optional)* **Cluster hint** — `7g`, `7`, `new`, `others`, or blank.
5. *(Optional)* **Files involved** — comma-separated paths.
6. *(Optional)* Check the **Publish on merge** box to cut a draft `rc` release after auto-merge.
7. Submit the issue. The workflow reads the label and dispatches Copilot.

If validation fails (e.g. blank reproducer in `bug-repro`), the workflow labels the issue `copilot:invalid` and comments with what's missing. Edit the issue and re-add the mode label to retry.

---

## Cluster routing (fallback chain)

The hint resolution rule, applied in order:

1. **Existing letter** (`7g`, `12e`) → use it; if taken, advance to the next free letter.
2. **Number only** (`7`) → allocate the next free sub-cluster letter inside it.
3. **`new`** → create a brand-new cluster; pick the next free number; register in `test-clusters.md`, `showcase_clusters.py`, and the `pip_publish.yml` + `showcase_tests.yml` default selectors.
4. **`others` or blank** AND no existing cluster fits → place under `lex/test_project/tests/others/`.

Copilot writes a one-sentence justification in the PR description: *"Placed under cluster Nx because …"*.

---

## PR-gate checks

`copilot_pr_gate.yml` runs three checks on every PR Copilot opens; failure on any one of them blocks merge:

1. **Mode discovery** — the PR body must contain `Fixes #N`; the linked issue must have exactly one `copilot:<mode>` label.
2. **PR-shape contract** (`copilot_validate_pr_shape.py`) — file set, naming, body markers; per-mode required artifacts (see §7 of [the design spec](../superpowers/specs/2026-05-13-copilot-test-bot-design.md)).
3. **Run the new test** — modes A/C must pass; mode B is re-run with `@expectedFailure` stripped on a temp copy and must FAIL (otherwise the claimed bug is not reproducible).

Branch protection separately requires `showcase_tests / Run cluster showcase & send report` — the gate workflow does not invoke that itself.

---

## Publish on merge

`copilot_publish_after_merge.yml` watches for merged Copilot PRs with both `auto-merge` and `publish-on-merge` labels. When both are set AND the repo variable `COPILOT_AUTO_PUBLISH_ENABLED == "true"`:

1. Find the latest `vX.Y.ZrcN` release tag.
2. Bump to `vX.Y.Zrc(N+1)` (rc-only — never major/minor/patch).
3. Create a **draft** release with release notes listing every PR merged since the previous tag.
4. The existing `pip_publish.yml` fires on `release: created` and finishes the publish.

Default is **off** — flip `COPILOT_AUTO_PUBLISH_ENABLED` to `"true"` only after at least one regression-mode round-trip has been observed end-to-end. The draft step exists as an audit artifact in the GitHub Releases UI; `pip_publish.yml` triggers on `release: created` (which fires for drafts too), so PyPI upload begins immediately and there is no reliable abort window between draft creation and publish. Per-run aborts must happen by flipping `COPILOT_AUTO_PUBLISH_ENABLED` to `"false"` before merging the PR.

---

## Configuration prerequisites

| Setting | Where | Value |
|---|---|---|
| Issue template | `.github/ISSUE_TEMPLATE/copilot-test-request.yml` | Shipped with this PR |
| Labels | Repo Settings → Labels | `copilot:regression`, `copilot:bug-repro`, `copilot:fix-and-test`, `copilot:invalid`, `auto-merge`, `needs-human-review`, `publish-on-merge` |
| Copilot coding agent enabled on `lex-app` | Org → Copilot → Policies | On |
| Branch protection on `lex-app-v2` | Repo Settings → Branches | Required check: `showcase_tests`; `copilot-swe-agent[bot]` in the bypass list; auto-merge enabled at repo level |
| Repo secret `COPILOT_PAT` | Repo Settings → Secrets | PAT able to assign `copilot-swe-agent[bot]` (mirror from `lex-app-docs`) |
| Repo variable `COPILOT_AUTO_PUBLISH_ENABLED` | Repo Settings → Variables | `"false"` initially |

---

## Failure modes and recovery

| Symptom | Cause | Recovery |
|---|---|---|
| Issue labeled `copilot:invalid` immediately | Two mode labels, blank behaviour, or missing reproducer for B/C | Edit the issue, fix the field, re-add the mode label |
| Copilot's PR labeled `copilot:invalid` | PR-shape check failed — see the PR comment for the list | Re-trigger by closing the PR and re-adding the mode label to the original issue, OR fix manually |
| Mode-B PR blocked: "test passed without @expectedFailure" | The bug being claimed is no longer reproducible | Close the issue (the bug may already be fixed) or re-file as `copilot:regression` |
| `showcase_tests` red on auto-merge PR | New test depends on a missing fixture or surfaces a flake | PR sits open; same triage as any human-authored PR |
| Two Copilot runs append `progress/session-log.md` simultaneously | Append-only conflict | Second run retries cleanly after the first lands |

---

## Design choices — why each piece looks the way it does

### Why three workflows instead of one?

Each workflow listens to a different GitHub event (`issues: labeled`, `pull_request`, `pull_request: closed`). Folding them into one would require polling. Splitting them keeps each workflow's `on:` block narrow and its permission set minimal.

### Why assemble the prompt at runtime?

`copilot_assemble_prompt.py` reads four files from `lex/test_project/test-plan/` every run. When the test-plan rules evolve, edit the test-plan docs — the next workflow run sees the new wording automatically. No prompt-versioning to maintain in YAML.

### Why split `progress.md`?

The original `progress.md` mixed a high-churn dashboard table + Known Bugs Tracker with stable methodology + run instructions. Every session edit was a merge-conflict candidate, and the Copilot PR-shape check could not enforce "append one row" mechanically. The split (`progress/conventions.md`, `progress/dashboard.md`, `progress/session-log.md`) mirrors the volatility — each PR touches the smallest file.

### Why does mode B strip the decorator and assert failure?

A `@expectedFailure` test is reported as passing whether the body raises or not — so a Copilot mistake (e.g. an `assert True`) would land as a "reproduces BUG-NNN" gate-green merge. Re-running the body with the decorator stripped forces an actual reproduction check.

### Why no auto-merge on mode C?

Mode C's PR ships a behaviour change. Auto-merge would mean "Copilot fix lands with no human reading the diff". The 50-line cap + required `### Source changes` body section reduce review effort but never replace the human read.

### Why a draft release on the publish path?

The draft surfaces in the GitHub Releases UI with the auto-generated notes — an audit artifact for after-the-fact review. Note: `pip_publish.yml` triggers on `release: created`, which fires for draft releases too, so PyPI publish begins immediately and there is no reliable abort window between draft and upload. Per-run aborts must happen by flipping `COPILOT_AUTO_PUBLISH_ENABLED` to `"false"` before merging the PR; the draft is for visibility, not a manual gate.

### Why rc-only on publish bumps?

x/y/z bumps are product decisions — a release with no humans reading the diff is not the right place to make them. The publish path explicitly refuses any non-rc current tag (`compute_next_rc` raises `ValueError`).

### Why a PAT for Copilot assignment?

The Copilot coding agent requires a PAT to be assigned to an issue — `GITHUB_TOKEN` and GitHub App tokens both lack the needed scope today. This is the same restriction `lex-app-docs` already lives with for `auto-update-docs.yml`. Reused, not added.

---

> **See also:** [Automated Documentation Pipeline](automated-docs-pipeline.md) | [CI Overview](ci-overview.md) | [Design Spec](../superpowers/specs/2026-05-13-copilot-test-bot-design.md)
```

- [ ] **Step 2: Verify links resolve**

Run: `grep -oE '\]\([^)]+\)' docs/ci-cd/copilot-test-bot.md | sort -u`
For each relative target, confirm the file exists.

- [ ] **Step 3: Commit**

```bash
git add docs/ci-cd/copilot-test-bot.md
git commit -m "docs(ci-cd): add copilot-test-bot user-facing doc — modes, routing, gate, publish, design choices"
```

---

## Task 14: Pre-flight manual-config checklist (final task)

**Files:**
- None — this is a tracking task. The output is a comment on the integrating PR enumerating the manual steps a maintainer must take before the workflows function end-to-end.

The three workflows + helpers ship as code, but six things only a repo admin can do gate end-to-end activation. Document them in the PR description so they aren't lost.

- [ ] **Step 1: Verify all tests pass + workflows lint**

Run:
```bash
pytest .github/scripts/tests/ -v
```
Expected: all tests pass (8 + 15 + 10 = 33 tests).

Then sanity-check the three workflow YAMLs parse:
```bash
python - <<'PY'
import yaml, pathlib, sys
for f in pathlib.Path(".github/workflows").glob("copilot_*.yml"):
    yaml.safe_load(f.read_text())
    print(f"OK {f}")
PY
```
Expected: each file prints `OK`.

- [ ] **Step 2: Author the integrating PR's description with the checklist**

When opening the PR that lands this whole plan, include this section in the PR body verbatim — these are the only steps the workflows can't do themselves:

```markdown
## Before merging — manual repo configuration

The following six settings must be in place before this PR's workflows function end-to-end.
Document who applied each one in this comment thread so the next operator can audit.

- [ ] **Create labels** (Repo Settings → Labels): `copilot:regression`, `copilot:bug-repro`,
      `copilot:fix-and-test`, `copilot:invalid`, `auto-merge`, `needs-human-review`,
      `publish-on-merge`.
- [ ] **Add repo variable** `COPILOT_AUTO_PUBLISH_ENABLED` (Repo Settings → Variables),
      initial value `"false"`.
- [ ] **Add repo secret** `COPILOT_PAT` mirrored from `lex-app-docs`.
- [ ] **Enable Copilot coding agent on `lex-app`** at the org Copilot Policies level
      (currently enabled only on `lex-app-docs`).
- [ ] **Branch protection on `lex-app-v2`** (Repo Settings → Branches → `lex-app-v2`):
      (a) **Required status check** must include `showcase_tests / Run cluster showcase
      & send report` — without it the Copilot PR-gate becomes the only blocking check
      and any green-gate PR auto-merges regardless of showcase results.
      (b) Require **1 review** for non-bot PRs.
      (c) Add `copilot-swe-agent[bot]` to the **"Allow specified actors to bypass required
      pull requests"** list. **Apply (a) and (b) BEFORE adding the bot to the bypass list**
      — otherwise the first Copilot PR can land before the required-check rule is in place.
      (d) Enable repository-level auto-merge.
- [ ] **Create the `lex-maintainers` team** (Org → Teams) and add the maintainers who
      should be auto-requested as reviewers on every mode-C (`fix-and-test`) Copilot PR.
      `copilot_pr_gate.yml` requests them via `gh pr edit --add-reviewer lex-maintainers`;
      if the team is missing the workflow degrades gracefully with a `::warning::` but
      mode-C PRs land without an automatic reviewer assignment.

After all six are checked, fire `copilot_test_bot.yml` via `workflow_dispatch` against
a hand-filed `copilot:regression` test issue and confirm one full round-trip
(issue → Copilot PR → green PR-gate → auto-merge) before flipping
`COPILOT_AUTO_PUBLISH_ENABLED` to `"true"`.
```

- [ ] **Step 3: Commit (this task adds nothing to the tree — it's a PR-description item)**

No commit. The checklist lives in the integrating PR's description, not in a file.

---

## Self-review notes

- **Spec coverage:** Tasks 1-5 cover §8 (progress decomposition). Task 6 covers §4 (issue template). Tasks 7-8 cover §6 (prompt assembly) + §4 (entry workflow trigger + validation). Tasks 9-10 cover §7 (PR-gate check matrix + mode-B safeguard + mode-C routing). Tasks 11-12 cover §9 (publish path + kill-switch). Task 13 covers §13 (user-facing doc with design-choice rationale). Task 14 covers §10 + §11 (config prerequisites + secrets).
- **No placeholders:** every code block is complete; no TBD/TODO; the workflow YAMLs are end-to-end runnable; each pytest test has a real assertion.
- **Type consistency:** `IssueInput`/`Mode` used the same way across the prompt-assembly script + its tests; `PRFile`/`ValidationResult` matched between validator script + tests; `compute_next_rc(tag: str) -> str` is the single-arg shape used everywhere.
- **The `lex test` venv path** in the PR-gate workflow's "run the test" steps mirrors the conventions.md instruction (`source /path/to/your-project/.venv/bin/activate`). On GitHub-hosted runners that path won't exist — the `|| true` tolerates the absence. If the project uses a different runner setup, this step is the one to revisit.
