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
# Copilot task

> Assembled from issue #{issue.number}. Read the Golden Rule first.

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

**Title:** {issue.title}

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
