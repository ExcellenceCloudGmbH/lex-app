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


# GitHub caps issue bodies at 65,536 bytes. Stay well under so a slow drift in
# conventions.md or the Golden Rule paragraph doesn't suddenly tip us over at
# `gh issue create` time. The big files (test-clusters.md, test-writing-plan.md)
# are referenced by path, not inlined — Copilot reads them from the repo.
_MAX_BODY_BYTES = 60_000

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

Write a failing test for the documented behaviour, then make the **smallest source change** that makes it pass. Source diff must be ≤ 50 changed lines. List every source file you touched in the PR description under a `### Source changes` heading with one bullet per file and a one-line rationale. **No auto-merge** — this PR is routed to human review.
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
    conventions_md = _read(test_plan_dir / "progress" / "conventions.md")
    # test-clusters.md and test-writing-plan.md are referenced by path, not
    # inlined (they're too large for GitHub's 64KB issue body cap — clusters
    # alone is ~160KB). We still _verify they exist_ so a typo or a moved file
    # blows up here at prompt-assembly time, not later when Copilot follows a
    # dead link from the issue body.
    for required in ("test-clusters.md", "test-writing-plan.md"):
        if not (test_plan_dir / required).exists():
            raise FileNotFoundError(f"required test-plan file missing: {test_plan_dir / required}")

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

    body = f"""\
# Copilot task

> Assembled from issue #{issue.number}. Read the Golden Rule first.

---

{golden_block}

---

## Research before you write code

The Golden Rule above tells you *not* to mirror the implementation. This tells you what to do instead — **gather intent before writing anything**:

1. **Read the docs that describe intent**, not just the code: `docs/features/`, `docs/reference/`, `docs/tutorial/`. Derive the behaviour you assert from what the framework is *trying to achieve* + what a customer would reasonably expect.
2. **Read the public API docstrings** of the classes/functions involved, and skim the existing cluster tests for that area to match established patterns.
3. **Check for an existing mechanism before inventing one.** Before adding a new store, cache, registry, or helper, search the repo for one that already does the job — a second, parallel home for state that already has one is a common source of subtle divergence bugs. Reuse the established mechanism rather than reinventing it.
4. **Restate what the request means before designing — especially its scope.** Put the behaviour in your own words and pin down its *scope and propagation*: words like "every / all / always / each" hide a boundary — does the change apply at the **entry point only**, or **recursively** to everything it triggers downstream? If that boundary (or any other contract) is genuinely ambiguous and the docs don't settle it, **state the interpretation you chose and your reasoning explicitly in the PR description**, pick the option a customer would expect, and make the alternative you rejected visible so a reviewer can correct it in one comment — don't silently guess.

---

## Test-plan conventions

{conventions_md.strip()}

---

## Cluster catalogue

**Read `lex/test_project/test-plan/test-clusters.md` in this repo before picking a cluster.** That file owns every cluster's scope, scenarios, and status. The cluster routing rules below depend on it.

---

## Writing-plan rules (file naming, scenario IDs, sub-clusters)

**Read `lex/test_project/test-plan/test-writing-plan.md` in this repo before creating the test file.** That file owns file naming, scenario IDs, and sub-cluster allocation rules.

---

{_MODE_BLOCKS[issue.mode]}

---

## The issue

**Title:** {issue.title}

**Behaviour:**

{issue.behaviour.strip()}

**Reproducer:**

{reproducer_block}

**Cluster hint:** {hint_block}

**Files involved:**

{files_block}

---

## Surfaces and clusters — one feature may need tests in multiple clusters

**The authoritative rule is in the *Test-plan conventions* above — "Enumerate Behaviour Surfaces Before Allocating Clusters." Apply it; it is the single source this prompt and every local agent share.** This section only says how it maps onto a Copilot PR:

1. **Enumerate the surfaces first.** Per the conventions rule, list every externally-observable behaviour the change introduces — each public entry point, **each distinct execution path the same operation can take** (one operation may run by more than one route — cover each route, don't assume one generalises to the others), each state transition, each persisted/emitted side-effect, each error/edge path. A surface counts as covered only when a test drives it end-to-end through the public entry point.
2. **Map each surface to its owning cluster.** The cluster hint names the **primary** cluster (the headline surface). Every surface that lives in a *different* cluster is a **secondary** cluster that gets its own test file. Map by *what the surface is*, not where the source line lives:
   - Produces audit-log entries → test in `audit_logging/`.
   - Drives calculation state transitions → test in `calculations/`.
   - Must respect permission rules → test in `permissions/`.
   - Emits WebSocket signals → test in `signals_ws/`.
   - Goes through the REST/serializer surface → test in `api_layer/` or `serializers/`.
3. State the decomposition in the PR description, one line per file: *"Placed `tests/<slug>/test_<Nx>_<short>.py` under cluster N because …"*. If a surface genuinely cannot be exercised here, say so explicitly in the PR body — never silently drop it.

**Mode-specific scope:**

- **regression** — multi-cluster decomposition encouraged when the feature has real cross-cluster contracts.
- **bug-repro** — exactly **one** new test file (the `@expectedFailure` repro). Cross-cluster effects are validated by the repro itself, not by extra test files.
- **fix-and-test** — exactly **one** new test file (the failing test the source change makes pass). The 50-line source-diff cap is meaningful only with a single test in scope.

---

## Cluster routing for each new test file (apply in order, first match wins)

**Folder naming:** cluster folders under `lex/test_project/tests/` are named with the cluster's **descriptive slug**, not `cluster_N/`. Existing folders include `init/`, `crud_api/`, `validation_hooks/`, `permissions/`, `history/`, `audit_logging/`, `calculations/`, `calculation_logging/`, `celery_async/`, `signals_ws/`, `api_layer/`, `serializers/`, `queries/`, `exports/`, `journeys/`, `stress/`. Each cluster folder has its own `models.py` — **do not** create a central `models/` directory.

For each test file you create (primary + each secondary):

1. Hint names an **existing cluster letter** (e.g. `7g`) → use that cluster's folder. If the letter is taken, advance to the next free letter per `test-writing-plan.md`.
2. Hint names just a **cluster number** (e.g. `7`) → use that cluster's folder; allocate the next free sub-cluster letter inside it.
3. Secondary cluster (chosen by you, not in the hint) → allocate the next free sub-cluster letter inside that cluster.
4. Hint is **`others`** or **blank** AND no existing cluster fits → place under `lex/test_project/tests/others/` (create if missing) with generic numbering. This is the normal fallback.
5. Hint is **`new`** → create a new cluster (rare). Pick the next free cluster number, choose a descriptive slug for the folder name, add a `test-clusters.md` entry, register in `.github/scripts/showcase_clusters.py`, update default selectors in `pip_publish.yml` + `showcase_tests.yml`. Prefer `others/` over a new cluster unless the feature is a genuinely new surface area.

---

## Required deliverables in the PR

- One or more new test files under `lex/test_project/tests/<slug>/test_<Nx>_<short>.py` — one per cluster involved (regression mode may have several; bug-repro and fix-and-test have exactly one).
- `lex/test_project/test-plan/test-clusters.md` updated (status / scenario range for **each** touched (sub-)cluster).
- One new row appended to the bottom of `lex/test_project/test-plan/progress/session-log.md` summarising all touched clusters in this PR.
- Bump the matching per-cluster status row in `lex/test_project/test-plan/progress/dashboard.md`.
- If your work corresponds to a planned batch in `lex/test_project/test-plan/test-writing-plan.md`, append/update that batch row (scenario range, files covered, test classes, fixtures, status) so the allocation tracker stays consistent.
- Mode B/C only: one new BUG-NNN row in `lex/test_project/test-plan/known-bugs.md`.
- New-cluster placement only: register the new cluster in `.github/scripts/showcase_clusters.py` and update default selectors in `pip_publish.yml` + `showcase_tests.yml`.
- Touch nothing outside the allowed file set, except the source fix in mode `fix-and-test`.

End the PR body with the exact line: `Fixes #{issue.number}` so the gate workflow can find this issue.
"""
    # Belt to keep the bug fixed: if conventions.md or the Golden Rule grow
    # large enough to threaten the 64KB cap, fail loud in CI rather than at
    # `gh issue create` time (which surfaced as a cryptic
    # "GraphQL: Body is too long" in the 2026-05-21 production run).
    size = len(body.encode("utf-8"))
    if size > _MAX_BODY_BYTES:
        raise ValueError(
            f"assembled prompt is {size} bytes — exceeds {_MAX_BODY_BYTES}-byte cap. "
            "Trim conventions.md / the Golden Rule paragraph in index.md, or move "
            "more content behind a 'read this file in the repo' pointer."
        )
    return body


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
