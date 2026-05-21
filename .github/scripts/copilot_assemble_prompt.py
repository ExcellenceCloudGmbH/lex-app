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

## Cluster decomposition — one feature may need tests in multiple clusters

A feature's contract often spans clusters. The cluster hint names the **primary** cluster (the feature's headline behaviour). Your job is to identify **secondary** clusters whose contracts the feature also touches, and add a test file in each.

**How to decide (regression mode):**

1. **Primary cluster** — owns the feature's headline behaviour. The "selling point" test goes here. This is what the user hinted at (or what cluster routing below picks if no hint).
2. **Secondary clusters** — for every observable contract the feature is responsible for that lives in a *different* cluster, add a separate test file in that cluster's folder. Typical cross-cluster contracts:
   - Produces audit-log entries → test in `audit_logging/`.
   - Drives calculation state transitions → test in `calculations/`.
   - Must respect permission rules → test in `permissions/`.
   - Emits WebSocket signals → test in `signals_ws/`.
   - Goes through the REST/serializer surface → test in `api_layer/` or `serializers/`.
   Read the **Behaviour** and **Reproducer** fields below carefully; each contractual guarantee the user lists is a candidate for its own cross-cluster test file.
3. State the decomposition in the PR description, one line per file: *"Placed `tests/<slug>/test_<Nx>_<short>.py` under cluster N because …"*

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
