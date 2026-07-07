# Test-Plan Scalability Restructure — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Shard `lex/test_project/test-plan/` into per-cluster directories with machine-readable allocation state, session fragments, and a generated dashboard — so agent reads stay bounded and concurrent PRs stop corrupting the plan.

**Architecture:** Two new stdlib+PyYAML scripts under `.github/scripts/` — `test_plan_aggregates.py` (build/check/validate, permanent) and `test_plan_split.py` (one-shot migration with a fact-preservation audit). All consumers (PR gate, prompt assembler, skill, instructions, AGENTS.md) are re-pointed in the same change. Spec: [`docs/superpowers/specs/2026-07-07-test-plan-scalability-restructure-design.md`](../specs/2026-07-07-test-plan-scalability-restructure-design.md).

**Tech Stack:** Python 3.12, PyYAML (already a CI dependency — `docs_mirror.py` imports it), pytest for the script tests (`.github/scripts/tests/`), GitHub Actions.

**Verification model:** Scripts are TDD'd. The migration itself is verified by the fact-preservation audit (scenario-ID/letter/BUG/session sets extracted from old files must equal the new tree) plus `validate` + `check` green, plus the existing `test_prompt_surface_parity.py`.

---

## Context for an engineer with zero repo knowledge

- The test-plan lives at `lex/test_project/test-plan/`. Three monoliths get retired:
  `test-clusters.md` (212 KB — cluster definitions, cut at `## N.` headings, preamble is
  philosophy), `test-writing-plan.md` (67 KB — batch blocks, cut at `## Cluster N`
  headings, plus non-cluster sections §§5–9 that move to `clusters/README.md`), and
  `progress/session-log.md` (232 KB — one markdown table, one row per session).
- Cluster number → test-folder slug: 1 `init`, 2 `crud_api`, 3 `validation_hooks`,
  4 `permissions`, 5 `history`, 6 `audit_logging`, 7 `calculations` (also owns
  `calculation_logging`), 8 `celery_async`, 9 `signals_ws`, 10 `api_layer`, 11 `stress`,
  12 `serializers`, 13 `exports`, 14 `queries`. Folders `fixtures`, `journeys`,
  `gate_selftest`, `others` are cluster-exempt.
- The PR gate (`.github/workflows/copilot_pr_gate.yml`) builds `files.json` with
  per-file `{path, additions, deletions, status}` and calls
  `copilot_validate_pr_shape.py`. The prompt assembler
  (`copilot_assemble_prompt.py`) inlines `index.md`'s Golden-Rule block and
  `progress/conventions.md`, and points at the monoliths by path.
- Script tests live in `.github/scripts/tests/` and run with plain `pytest`.
- Run script tests: `python -m pytest .github/scripts/tests/ -v` (from repo root,
  with the project venv active: `source project_example/.venv/bin/activate`).

---

### Task 1: `test_plan_aggregates.py` — loader, dashboard build, freshness check

**Files:**
- Create: `.github/scripts/test_plan_aggregates.py`
- Create: `.github/scripts/tests/test_test_plan_aggregates.py`

- [ ] **Step 1: Write the failing tests**

```python
# .github/scripts/tests/test_test_plan_aggregates.py
"""Tests for the test-plan aggregates tool (build / check / validate).

Fixture strategy: build a miniature sharded test-plan + test tree in tmp_path,
then run the real functions against it. No repo files are touched.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from test_plan_aggregates import (
    AllocationError,
    build_dashboard,
    check_dashboard,
    load_clusters,
    validate_allocations,
)


def make_plan(tmp_path: Path) -> tuple[Path, Path]:
    """Two clusters (7 owns two test dirs) + a matching test tree."""
    plan = tmp_path / "test-plan"
    tests = tmp_path / "tests"
    c1 = plan / "clusters" / "01-init"
    c7 = plan / "clusters" / "07-calculations"
    c1.mkdir(parents=True)
    c7.mkdir(parents=True)
    (plan / "progress").mkdir()
    c1.joinpath("allocation.yaml").write_text(
        "cluster: 1\n"
        "slug: init\n"
        "title: Init — Project Bootstrap\n"
        "max_scenario: 20\n"
        "letters:\n"
        "  a:\n"
        "    title: lex setup scaffolding\n"
        "    scenarios: 1.1-1.7\n"
        "    status: complete\n"
        "    tests: {pass: 7, skip: 0, xfail: 0}\n"
        "    note: ''\n"
    )
    c7.joinpath("allocation.yaml").write_text(
        "cluster: 7\n"
        "slug: calculations\n"
        "title: Calculation State Machine\n"
        "test_dirs: [calculations, calculation_logging]\n"
        "max_scenario: 12\n"
        "letters:\n"
        "  a:\n"
        "    title: Atomic happy path\n"
        "    scenarios: 7.1-7.12\n"
        "    status: complete\n"
        "    tests: {pass: 11, skip: 0, xfail: 1}\n"
        "    note: BUG-001\n"
    )
    for d in ("init", "calculations", "calculation_logging", "fixtures"):
        (tests / d).mkdir(parents=True)
    (tests / "init" / "test_1a_setup.py").write_text(
        '"""Scenario 1.1: scaffolding.\nScenario 1.7: idempotent."""\n'
    )
    (tests / "calculations" / "test_7a_atomic.py").write_text(
        '"""Scenario 7.1: IN_PROGRESS write.\nScenario 7.12: error capture."""\n'
    )
    return plan, tests


class TestLoad:
    def test_loads_clusters_sorted_by_number(self, tmp_path):
        plan, _ = make_plan(tmp_path)
        clusters = load_clusters(plan)
        assert [c.number for c in clusters] == [1, 7]
        assert clusters[0].slug == "init"
        assert clusters[0].test_dirs == ["init"]          # defaulted from slug
        assert clusters[1].test_dirs == ["calculations", "calculation_logging"]

    def test_dir_name_must_match_yaml(self, tmp_path):
        plan, _ = make_plan(tmp_path)
        bad = plan / "clusters" / "02-crud_api"
        bad.mkdir()
        bad.joinpath("allocation.yaml").write_text(
            "cluster: 3\nslug: crud_api\ntitle: X\nmax_scenario: 1\nletters: {}\n"
        )
        with pytest.raises(AllocationError, match="02-crud_api"):
            load_clusters(plan)

    def test_bad_status_rejected(self, tmp_path):
        plan, _ = make_plan(tmp_path)
        y = plan / "clusters" / "01-init" / "allocation.yaml"
        y.write_text(y.read_text().replace("status: complete", "status: done"))
        with pytest.raises(AllocationError, match="status"):
            load_clusters(plan)


class TestBuildCheck:
    def test_build_writes_dashboard_with_generated_header(self, tmp_path):
        plan, _ = make_plan(tmp_path)
        build_dashboard(plan)
        text = (plan / "progress" / "dashboard.md").read_text()
        assert "GENERATED" in text
        assert "Calculation State Machine" in text
        assert "BUG-001" in text
        assert "| 7a |" in text

    def test_check_passes_when_fresh_fails_when_stale(self, tmp_path):
        plan, _ = make_plan(tmp_path)
        build_dashboard(plan)
        assert check_dashboard(plan) == []
        dash = plan / "progress" / "dashboard.md"
        dash.write_text(dash.read_text() + "\nmanual edit\n")
        assert check_dashboard(plan) != []

    def test_build_is_deterministic(self, tmp_path):
        plan, _ = make_plan(tmp_path)
        build_dashboard(plan)
        first = (plan / "progress" / "dashboard.md").read_text()
        build_dashboard(plan)
        assert (plan / "progress" / "dashboard.md").read_text() == first
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest .github/scripts/tests/test_test_plan_aggregates.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'test_plan_aggregates'`

- [ ] **Step 3: Implement loader + build + check**

```python
# .github/scripts/test_plan_aggregates.py
"""Aggregates + consistency tooling for the sharded test-plan.

Design: docs/superpowers/specs/2026-07-07-test-plan-scalability-restructure-design.md

Subcommands (all take --plan-dir / --tests-dir, defaulting to the repo paths):
  build     regenerate progress/dashboard.md from clusters/*/allocation.yaml
  check     exit 1 if progress/dashboard.md differs from a fresh build
  validate  allocation state vs the test tree (letters, scenario IDs, dir claims)

`allocation.yaml` is the ONLY input to the dashboard — never parse prose.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_PLAN_DIR = Path("lex/test_project/test-plan")
DEFAULT_TESTS_DIR = Path("lex/test_project/tests")

# Test folders that deliberately have no owning cluster.
EXEMPT_TEST_DIRS = {"fixtures", "journeys", "gate_selftest", "others", "__pycache__"}

VALID_STATUSES = {"planned", "in-flight", "complete", "blocked", "rolled-back"}

GENERATED_BANNER = (
    "<!-- ⚙ GENERATED by .github/scripts/test_plan_aggregates.py build — "
    "DO NOT HAND-EDIT. Edit clusters/*/allocation.yaml and rebuild. -->"
)

LETTER_FILE_RE = re.compile(r"^test_(?P<num>\d+)(?P<letter>[a-z])_[A-Za-z0-9_]+\.py$")
SCENARIO_RE = re.compile(r"Scenario (?P<num>\d+)\.(?P<sid>\d+[a-z]?)\b")
CLUSTER_DIR_RE = re.compile(r"^(?P<num>\d{2})-(?P<slug>[a-z0-9_]+)$")


class AllocationError(Exception):
    """Schema or consistency violation in allocation state."""


@dataclass
class Letter:
    letter: str
    title: str
    scenarios: str
    status: str
    tests: dict
    note: str


@dataclass
class Cluster:
    number: int
    slug: str
    title: str
    max_scenario: int
    test_dirs: list[str]
    letters: list[Letter]
    path: Path  # the clusters/NN-<slug>/ directory


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise AllocationError(msg)


def load_clusters(plan_dir: Path) -> list[Cluster]:
    """Load and schema-check every clusters/*/allocation.yaml."""
    clusters_dir = plan_dir / "clusters"
    _require(clusters_dir.is_dir(), f"missing {clusters_dir}")
    out: list[Cluster] = []
    for d in sorted(p for p in clusters_dir.iterdir() if p.is_dir()):
        m = CLUSTER_DIR_RE.match(d.name)
        _require(m is not None, f"cluster dir {d.name!r} must match NN-<slug>")
        yml = d / "allocation.yaml"
        _require(yml.is_file(), f"missing {yml}")
        data = yaml.safe_load(yml.read_text())
        for key in ("cluster", "slug", "title", "max_scenario", "letters"):
            _require(key in data, f"{yml}: missing key {key!r}")
        _require(
            int(data["cluster"]) == int(m.group("num"))
            and data["slug"] == m.group("slug"),
            f"{d.name}: dir name disagrees with YAML "
            f"(cluster={data['cluster']}, slug={data['slug']})",
        )
        letters = []
        for letter, spec in (data["letters"] or {}).items():
            _require(
                re.fullmatch(r"[a-z]", str(letter)) is not None,
                f"{yml}: letter {letter!r} must be a single lowercase letter",
            )
            _require(
                spec.get("status") in VALID_STATUSES,
                f"{yml}: letter {letter!r} has invalid status "
                f"{spec.get('status')!r} (valid: {sorted(VALID_STATUSES)})",
            )
            letters.append(
                Letter(
                    letter=str(letter),
                    title=str(spec.get("title", "")),
                    scenarios=str(spec.get("scenarios", "")),
                    status=spec["status"],
                    tests=spec.get("tests") or {"pass": 0, "skip": 0, "xfail": 0},
                    note=str(spec.get("note", "") or ""),
                )
            )
        letters.sort(key=lambda l: l.letter)
        out.append(
            Cluster(
                number=int(data["cluster"]),
                slug=data["slug"],
                title=str(data["title"]),
                max_scenario=int(data["max_scenario"]),
                test_dirs=list(data.get("test_dirs") or [data["slug"]]),
                letters=letters,
                path=d,
            )
        )
    nums = [c.number for c in out]
    _require(len(nums) == len(set(nums)), f"duplicate cluster numbers: {nums}")
    out.sort(key=lambda c: c.number)
    return out


def render_dashboard(clusters: list[Cluster]) -> str:
    lines = [
        "# Test-Suite Dashboard",
        "",
        GENERATED_BANNER,
        "",
        "> Built from `clusters/*/allocation.yaml`. Per-batch narrative lives in each",
        "> cluster's `batches.md`; scenario intent in `cluster.md`; session narrative",
        "> in `progress/sessions/`.",
        "",
        "## At a glance",
        "",
        "| Cluster | Batches | Max scenario | Pass | Skip | Xfail |",
        "|---|---|---|---|---|---|",
    ]
    for c in clusters:
        t = {"pass": 0, "skip": 0, "xfail": 0}
        for l in c.letters:
            for k in t:
                t[k] += int(l.tests.get(k, 0))
        lines.append(
            f"| {c.number}. {c.title} | {len(c.letters)} | "
            f"{c.max_scenario} | {t['pass']} | {t['skip']} | {t['xfail']} |"
        )
    for c in clusters:
        lines += [
            "",
            f"## {c.number}. {c.title} (`{c.slug}`)",
            "",
            "| Batch | Title | Scenarios | Status | Pass | Skip | Xfail | Note |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for l in c.letters:
            lines.append(
                f"| {c.number}{l.letter} | {l.title} | {l.scenarios} | {l.status} | "
                f"{l.tests.get('pass', 0)} | {l.tests.get('skip', 0)} | "
                f"{l.tests.get('xfail', 0)} | {l.note} |"
            )
    return "\n".join(lines) + "\n"


def build_dashboard(plan_dir: Path) -> None:
    text = render_dashboard(load_clusters(plan_dir))
    (plan_dir / "progress" / "dashboard.md").write_text(text)


def check_dashboard(plan_dir: Path) -> list[str]:
    """Return a list of problems (empty = fresh)."""
    expected = render_dashboard(load_clusters(plan_dir))
    dash = plan_dir / "progress" / "dashboard.md"
    if not dash.is_file():
        return [f"{dash} is missing — run `test_plan_aggregates.py build`"]
    if dash.read_text() != expected:
        return [
            f"{dash} is stale — run `python .github/scripts/test_plan_aggregates.py build` "
            "and commit the result"
        ]
    return []
```

(The `validate` half and the CLI arrive in Task 2 — keep the module importable
without them for now by adding a temporary stub so Step 1's import list resolves:)

```python
def validate_allocations(plan_dir: Path, tests_dir: Path) -> list[str]:  # Task 2
    raise NotImplementedError
```

- [ ] **Step 4: Run the Task-1 test classes to verify they pass**

Run: `python -m pytest .github/scripts/tests/test_test_plan_aggregates.py -v -k "TestLoad or TestBuildCheck"`
Expected: PASS (the `validate` tests come in Task 2)

- [ ] **Step 5: Commit**

```bash
git add .github/scripts/test_plan_aggregates.py .github/scripts/tests/test_test_plan_aggregates.py
git commit -m "feat(test-plan): aggregates tool — allocation loader + generated dashboard (build/check)"
```

---

### Task 2: `test_plan_aggregates.py` — `validate` + CLI

**Files:**
- Modify: `.github/scripts/test_plan_aggregates.py`
- Modify: `.github/scripts/tests/test_test_plan_aggregates.py`

- [ ] **Step 1: Add the failing tests**

```python
# append to .github/scripts/tests/test_test_plan_aggregates.py

class TestValidate:
    def test_clean_tree_passes(self, tmp_path):
        plan, tests = make_plan(tmp_path)
        assert validate_allocations(plan, tests) == []

    def test_unclaimed_test_dir(self, tmp_path):
        plan, tests = make_plan(tmp_path)
        (tests / "mystery").mkdir()
        errs = validate_allocations(plan, tests)
        assert any("mystery" in e for e in errs)

    def test_letter_not_in_yaml(self, tmp_path):
        plan, tests = make_plan(tmp_path)
        (tests / "init" / "test_1z_rogue.py").write_text('"""Scenario 1.2: x."""\n')
        errs = validate_allocations(plan, tests)
        assert any("1z" in e for e in errs)

    def test_scenario_above_max(self, tmp_path):
        plan, tests = make_plan(tmp_path)
        (tests / "init" / "test_1a_more.py").write_text('"""Scenario 1.99: over."""\n')
        errs = validate_allocations(plan, tests)
        assert any("1.99" in e for e in errs)

    def test_duplicate_scenario_across_files(self, tmp_path):
        plan, tests = make_plan(tmp_path)
        (tests / "init" / "test_1a_dupe.py").write_text('"""Scenario 1.1: dupe."""\n')
        errs = validate_allocations(plan, tests)
        assert any("1.1" in e and "duplicate" in e.lower() for e in errs)

    def test_wrong_cluster_number_in_folder(self, tmp_path):
        plan, tests = make_plan(tmp_path)
        (tests / "init" / "test_7a_misplaced.py").write_text('"""Scenario 7.2: x."""\n')
        errs = validate_allocations(plan, tests)
        assert any("test_7a_misplaced" in e for e in errs)
```

- [ ] **Step 2: Run to verify the new class fails**

Run: `python -m pytest .github/scripts/tests/test_test_plan_aggregates.py -v -k TestValidate`
Expected: FAIL — `NotImplementedError`

- [ ] **Step 3: Implement `validate_allocations` + CLI (replace the stub)**

```python
def validate_allocations(plan_dir: Path, tests_dir: Path) -> list[str]:
    """Cross-check allocation.yaml claims against the test tree.

    Rules (spec §4/§5): every non-exempt test folder is claimed by exactly one
    cluster's test_dirs; every test_<N><letter>_*.py carries the owning cluster's
    number and a letter present in YAML; every `Scenario N.M` docstring ID for the
    owning cluster is <= max_scenario and unique across the cluster's files.
    """
    errors: list[str] = []
    clusters = load_clusters(plan_dir)

    owner_by_dir: dict[str, Cluster] = {}
    for c in clusters:
        for d in c.test_dirs:
            if d in owner_by_dir:
                errors.append(
                    f"test dir {d!r} claimed by clusters "
                    f"{owner_by_dir[d].number} and {c.number}"
                )
            owner_by_dir[d] = c

    on_disk = {
        p.name for p in tests_dir.iterdir()
        if p.is_dir() and p.name not in EXEMPT_TEST_DIRS
        and not p.name.startswith(("_", "."))
    }
    for name in sorted(on_disk - set(owner_by_dir)):
        errors.append(
            f"test dir {name!r} has no owning cluster — add it to a cluster's "
            "test_dirs in allocation.yaml (or to EXEMPT_TEST_DIRS if deliberate)"
        )

    for c in clusters:
        seen: dict[str, Path] = {}  # scenario id -> first file
        yaml_letters = {l.letter for l in c.letters}
        for d in c.test_dirs:
            folder = tests_dir / d
            if not folder.is_dir():
                errors.append(f"cluster {c.number}: test dir {d!r} does not exist")
                continue
            for f in sorted(folder.glob("test_*.py")):
                m = LETTER_FILE_RE.match(f.name)
                if m:
                    if int(m.group("num")) != c.number:
                        errors.append(
                            f"{f}: filename cluster {m.group('num')} but folder "
                            f"{d!r} belongs to cluster {c.number}"
                        )
                    elif m.group("letter") not in yaml_letters:
                        errors.append(
                            f"{f}: letter {m.group('num')}{m.group('letter')} not "
                            f"declared in {c.path / 'allocation.yaml'}"
                        )
                text = f.read_text(errors="replace")
                for sm in SCENARIO_RE.finditer(text):
                    if int(sm.group("num")) != c.number:
                        continue  # cross-references to other clusters are prose
                    sid = f"{sm.group('num')}.{sm.group('sid')}"
                    base = int(re.match(r"\d+", sm.group("sid")).group())
                    if base > c.max_scenario:
                        errors.append(
                            f"{f}: scenario {sid} exceeds max_scenario="
                            f"{c.max_scenario} in {c.path / 'allocation.yaml'}"
                        )
                    prev = seen.get(sid)
                    if prev is not None and prev != f:
                        errors.append(
                            f"duplicate scenario {sid}: {prev} and {f}"
                        )
                    seen.setdefault(sid, f)
    return errors


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "check", "validate"))
    parser.add_argument("--plan-dir", type=Path, default=DEFAULT_PLAN_DIR)
    parser.add_argument("--tests-dir", type=Path, default=DEFAULT_TESTS_DIR)
    args = parser.parse_args()
    if args.command == "build":
        build_dashboard(args.plan_dir)
        print(f"wrote {args.plan_dir / 'progress' / 'dashboard.md'}")
        return 0
    problems = (
        check_dashboard(args.plan_dir)
        if args.command == "check"
        else validate_allocations(args.plan_dir, args.tests_dir)
    )
    for p in problems:
        print(f"- {p}")
    print("OK" if not problems else f"{len(problems)} problem(s)")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(_cli())
```

Also add `validate_allocations` to the test file's import (it is already there from
Task 1's import list).

- [ ] **Step 4: Run the full script test file**

Run: `python -m pytest .github/scripts/tests/test_test_plan_aggregates.py -v`
Expected: PASS (all classes)

- [ ] **Step 5: Commit**

```bash
git add .github/scripts/test_plan_aggregates.py .github/scripts/tests/test_test_plan_aggregates.py
git commit -m "feat(test-plan): aggregates tool — allocation validate + CLI"
```

---

### Task 3: `test_plan_split.py` — the one-shot migration tool

**Files:**
- Create: `.github/scripts/test_plan_split.py`
- Create: `.github/scripts/tests/test_test_plan_split.py`

The tool is committed (auditable), used once in Task 4, and deleted when the pointer
stubs are retired. It never edits prose — it only cuts, files, and reports.

- [ ] **Step 1: Write the failing tests (synthetic monolith snippets)**

```python
# .github/scripts/tests/test_test_plan_split.py
"""Tests for the one-shot test-plan migration splitter."""
from __future__ import annotations

from test_plan_split import (
    extract_facts,
    seed_allocation,
    split_clusters_md,
    split_sessions_md,
    split_writing_plan_md,
)

CLUSTERS_MD = """\
# Test Clusters

## Ordering: The User Journey

journey text.

## Testing Philosophy

golden rule text.

## 1. Init — Project Bootstrap

intro for cluster 1. Scenario 1.1 lives here.

### 1a. `lex setup` — scaffolding

body 1a.

## 2. CRUD via REST API

intro for cluster 2.
"""

WRITING_PLAN_MD = """\
# Test-Writing Plan — COMPLETE bucket (May 2026)

preamble.

## Conventions for this plan

conventions text.

## Cluster 1 — Init / Project Bootstrap (existing 1a–1n + new 1o)

### Batch 1o — Lazy imports ✅

- **Scenarios:** 1.110–1.124
- rows.

## Cluster 2 — CRUD via REST API (existing 2a–2e)

### Batch 2f — Model-entry mixins

- **Scenarios:** 2.30–2.41
- rows.

## 5. LATER bucket (deferred — keep in backlog)

later text.

## 6. Pending decisions blocking specific batches

decisions text.
"""

SESSIONS_MD = """\
# Session Log

header prose.

| Date | Session | What Was Done | Clusters Affected | Tests Added | Tests Passing |
|------|---------|---------------|-------------------|-------------|---------------|
| 2026-04-17 | 1 | Created test plan documentation | All | 0 | 0 |
| 2026-04-19 | 2 | Implemented Cluster 1 (17 tests). BUG-004 surfaced. | 1, 2 | 37 | 31 (6 expected failures) |
"""


class TestSplitClusters:
    def test_preamble_and_numbered_sections(self):
        preamble, sections = split_clusters_md(CLUSTERS_MD)
        assert "Testing Philosophy" in preamble
        assert sorted(sections) == [1, 2]
        title, body = sections[1]
        assert title == "Init — Project Bootstrap"
        assert "### 1a." in body


class TestSplitWritingPlan:
    def test_cluster_blocks_and_misc(self):
        misc, blocks = split_writing_plan_md(WRITING_PLAN_MD)
        assert sorted(blocks) == [1, 2]
        assert "Batch 1o" in blocks[1]
        assert "LATER bucket" in misc and "Pending decisions" in misc
        assert "Conventions for this plan" in misc


class TestSplitSessions:
    def test_rows_become_fragments(self):
        frags = split_sessions_md(SESSIONS_MD)
        assert len(frags) == 2
        assert frags[0]["date"] == "2026-04-17"
        assert frags[0]["session"] == 1
        assert frags[1]["clusters"] == "1, 2"
        assert "BUG-004" in frags[1]["prose"]


class TestSeedAllocation:
    def test_letters_and_max_from_batches(self):
        y = seed_allocation(1, "init", "Init — Project Bootstrap", WRITING_PLAN_MD)
        assert y["cluster"] == 1
        assert "o" in y["letters"]
        assert y["letters"]["o"]["status"] == "complete"   # ✅ marker
        assert y["max_scenario"] >= 124                     # from 1.110–1.124


class TestFacts:
    def test_fact_extraction_finds_ids_letters_bugs(self):
        facts = extract_facts(CLUSTERS_MD + WRITING_PLAN_MD + SESSIONS_MD)
        assert ("1", "1") in facts.scenario_ids       # Scenario 1.1
        assert ("1", "a") in facts.letters            # 1a heading
        assert "BUG-004" in facts.bugs
        assert "2026-04-19" in facts.dates
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest .github/scripts/tests/test_test_plan_split.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'test_plan_split'`

- [ ] **Step 3: Implement the splitter**

```python
# .github/scripts/test_plan_split.py
"""One-shot migration: split the test-plan monoliths into the sharded layout.

Design: docs/superpowers/specs/2026-07-07-test-plan-scalability-restructure-design.md §7.
Committed for audit; delete after the pointer stubs are retired.

Usage:
    python .github/scripts/test_plan_split.py --plan-dir lex/test_project/test-plan [--apply]

Without --apply it is a dry run: prints what it would write + the fact audit.
The fact audit HARD-FAILS the run if the {scenario-id, letter, BUG, date} sets
extracted from the old monoliths differ from the new tree.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

CLUSTER_SLUGS = {
    1: "init", 2: "crud_api", 3: "validation_hooks", 4: "permissions",
    5: "history", 6: "audit_logging", 7: "calculations", 8: "celery_async",
    9: "signals_ws", 10: "api_layer", 11: "stress", 12: "serializers",
    13: "exports", 14: "queries",
}
EXTRA_TEST_DIRS = {7: ["calculations", "calculation_logging"]}

CLUSTER_HEAD_RE = re.compile(r"^## (\d+)\.\s+(.*)$", re.M)
WP_CLUSTER_HEAD_RE = re.compile(r"^## Cluster (\d+)\s*[—-]?\s*(.*)$", re.M)
BATCH_HEAD_RE = re.compile(r"^### (?:Batch )?(\d+)([a-z])[.\s]*[—-]?\s*(.*)$", re.M)
SCENARIO_ID_RE = re.compile(r"\b(\d+)\.(\d+[a-z]?)\b")
SCENARIO_RANGE_RE = re.compile(r"(\d+)\.(\d+)\s*[–—-]\s*(?:\d+\.)?(\d+)")
LETTER_HEAD_RE = re.compile(r"^###+ .*?\b(\d+)([a-z])\b", re.M)
BUG_RE = re.compile(r"\bBUG-\d+\b")
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
SESSION_ROW_RE = re.compile(
    r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*(\d+)\s*\|(.*)\|([^|]*)\|([^|]*)\|([^|]*)\|\s*$",
    re.M,
)


def _split_at(regex: re.Pattern, text: str) -> tuple[str, list[tuple[int, str, str]]]:
    """Return (preamble, [(number, title, body)]) cutting text at heading matches."""
    matches = list(regex.finditer(text))
    if not matches:
        return text, []
    preamble = text[: matches[0].start()]
    out = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out.append((int(m.group(1)), m.group(2).strip(), text[m.start():end].rstrip() + "\n"))
    return preamble, out


def split_clusters_md(text: str) -> tuple[str, dict[int, tuple[str, str]]]:
    preamble, parts = _split_at(CLUSTER_HEAD_RE, text)
    return preamble, {n: (title, body) for n, title, body in parts}


def split_writing_plan_md(text: str) -> tuple[str, dict[int, str]]:
    """Cluster blocks by number; everything else (preamble, conventions,
    LATER/pending/order/forecast/rules sections) is returned as misc."""
    matches = list(WP_CLUSTER_HEAD_RE.finditer(text))
    blocks: dict[int, str] = {}
    misc_parts: list[str] = []
    pos = 0
    all_heads = sorted(
        [(m.start(), m) for m in matches]
        + [(m.start(), None) for m in re.finditer(r"^## \d+\.\s", text, re.M)],
        key=lambda t: t[0],
    )
    boundaries = [s for s, _ in all_heads] + [len(text)]
    head_at = {s: m for s, m in all_heads}
    if boundaries:
        misc_parts.append(text[: boundaries[0]])
    for i, start in enumerate(boundaries[:-1]):
        chunk = text[start : boundaries[i + 1]]
        m = head_at.get(start)
        if m is not None:
            blocks[int(m.group(1))] = chunk.rstrip() + "\n"
        else:
            misc_parts.append(chunk)
    return "".join(misc_parts), blocks


def split_sessions_md(text: str) -> list[dict]:
    frags = []
    for m in SESSION_ROW_RE.finditer(text):
        date, session, prose, clusters, added, tally = (g.strip() for g in m.groups())
        frags.append(
            {
                "date": date,
                "session": int(session),
                "prose": prose,
                "clusters": clusters,
                "tests_added": added,
                "suite_tally": tally,
            }
        )
    return frags


def seed_allocation(number: int, slug: str, title: str, sources: str) -> dict:
    """Best-effort allocation.yaml seed for one cluster from any concatenation of
    that cluster's plan prose (batches block + cluster section). Counts are
    zeroed with a 'seeded' note — Task 4's review step fills them from the
    pre-migration dashboard."""
    letters: dict[str, dict] = {}
    for m in BATCH_HEAD_RE.finditer(sources):
        if int(m.group(1)) != number:
            continue
        letter, rest = m.group(2), m.group(3)
        done = "✅" in rest or "✅" in sources[m.start(): m.start() + 200]
        letters.setdefault(
            letter,
            {
                "title": rest.replace("✅", "").strip(" —-"),
                "scenarios": "",
                "status": "complete" if done else "planned",
                "tests": {"pass": 0, "skip": 0, "xfail": 0},
                "note": "seeded by test_plan_split.py — verify",
            },
        )
    max_scenario = 0
    for m in SCENARIO_ID_RE.finditer(sources):
        if int(m.group(1)) == number:
            max_scenario = max(max_scenario, int(re.match(r"\d+", m.group(2)).group()))
    for m in SCENARIO_RANGE_RE.finditer(sources):
        if int(m.group(1)) == number:
            max_scenario = max(max_scenario, int(m.group(3)))
    return {
        "cluster": number,
        "slug": slug,
        "title": title,
        **({"test_dirs": EXTRA_TEST_DIRS[number]} if number in EXTRA_TEST_DIRS else {}),
        "max_scenario": max_scenario,
        "letters": dict(sorted(letters.items())),
    }


@dataclass
class Facts:
    scenario_ids: set = field(default_factory=set)  # {(cluster, sid)}
    letters: set = field(default_factory=set)       # {(cluster, letter)}
    bugs: set = field(default_factory=set)
    dates: set = field(default_factory=set)


def extract_facts(text: str) -> Facts:
    f = Facts()
    for m in SCENARIO_ID_RE.finditer(text):
        f.scenario_ids.add((m.group(1), m.group(2)))
    for m in LETTER_HEAD_RE.finditer(text):
        f.letters.add((m.group(1), m.group(2)))
    f.bugs = set(BUG_RE.findall(text))
    f.dates = set(DATE_RE.findall(text))
    return f


def _yaml_dump(d: dict) -> str:
    import yaml
    return yaml.safe_dump(d, sort_keys=False, allow_unicode=True, width=100)


def run(plan_dir: Path, apply: bool) -> int:
    clusters_md = (plan_dir / "test-clusters.md").read_text()
    writing_md = (plan_dir / "test-writing-plan.md").read_text()
    sessions_md = (plan_dir / "progress" / "session-log.md").read_text()

    preamble, cluster_sections = split_clusters_md(clusters_md)
    wp_misc, wp_blocks = split_writing_plan_md(writing_md)
    fragments = split_sessions_md(sessions_md)

    old_facts = extract_facts(clusters_md + writing_md + sessions_md)

    planned: dict[Path, str] = {}
    planned[plan_dir / "testing-philosophy.md"] = (
        "# Testing Philosophy\n\n"
        + preamble.split("\n", 1)[1].lstrip()  # drop the old H1
    )
    planned[plan_dir / "clusters" / "README.md"] = (
        "# Cluster Allocation — Conventions, Backlog, Pending Decisions\n\n"
        "> Absorbed from `test-writing-plan.md` (retired). Per-cluster batch\n"
        "> history lives in each `NN-<slug>/batches.md`.\n\n" + wp_misc.strip() + "\n"
    )
    for num, (title, body) in cluster_sections.items():
        slug = CLUSTER_SLUGS.get(num)
        if slug is None:
            print(f"WARNING: cluster {num} has no known slug — review manually")
            continue
        d = plan_dir / "clusters" / f"{num:02d}-{slug}"
        planned[d / "cluster.md"] = body
        batches = wp_blocks.get(num, f"## Cluster {num} — {title}\n\n(no batches recorded yet)\n")
        planned[d / "batches.md"] = batches
        planned[d / "allocation.yaml"] = _yaml_dump(
            seed_allocation(num, slug, title, body + "\n" + batches)
        )
    for frag in fragments:
        name = f"{frag['date']}-s{frag['session']:03d}.md"
        planned[plan_dir / "progress" / "sessions" / name] = (
            "---\n"
            f"date: {frag['date']}\n"
            f"clusters: [{frag['clusters']}]\n"
            f"tests_added: \"{frag['tests_added']}\"\n"
            f"suite_tally: \"{frag['suite_tally']}\"\n"
            "---\n\n"
            f"(migrated session {frag['session']})\n\n{frag['prose']}\n"
        )
    # Preserve the old dashboard for the Task-4 review, then it is deleted.
    planned[plan_dir / "progress" / "dashboard-pre-migration.md"] = (
        plan_dir / "progress" / "dashboard.md"
    ).read_text()

    new_facts = extract_facts("".join(planned.values()))
    missing = Facts(
        scenario_ids=old_facts.scenario_ids - new_facts.scenario_ids,
        letters=old_facts.letters - new_facts.letters,
        bugs=old_facts.bugs - new_facts.bugs,
        dates=old_facts.dates - new_facts.dates,
    )
    lost = any((missing.scenario_ids, missing.letters, missing.bugs, missing.dates))
    print(f"planned files: {len(planned)}")
    if lost:
        print("FACT AUDIT FAILED — facts present in old files but not the new tree:")
        for label, vals in (
            ("scenario ids", missing.scenario_ids), ("letters", missing.letters),
            ("bugs", missing.bugs), ("dates", missing.dates),
        ):
            if vals:
                print(f"  {label}: {sorted(vals)[:40]}")
        return 1
    print("fact audit: OK (scenario ids, letters, bugs, dates all preserved)")
    if apply:
        for path, text in planned.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        print("applied.")
    else:
        print("dry run — re-run with --apply to write.")
    return 0


def _cli() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plan-dir", type=Path, default=Path("lex/test_project/test-plan"))
    p.add_argument("--apply", action="store_true")
    a = p.parse_args()
    return run(a.plan_dir, a.apply)


if __name__ == "__main__":
    sys.exit(_cli())
```

- [ ] **Step 4: Run the splitter tests**

Run: `python -m pytest .github/scripts/tests/test_test_plan_split.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add .github/scripts/test_plan_split.py .github/scripts/tests/test_test_plan_split.py
git commit -m "feat(test-plan): one-shot migration splitter with fact-preservation audit"
```

---

### Task 4: Execute the migration (dry-run → review → apply → reconcile)

**Files:**
- Create: `lex/test_project/test-plan/clusters/**` (14 dirs × 3 files + README.md)
- Create: `lex/test_project/test-plan/testing-philosophy.md`
- Create: `lex/test_project/test-plan/progress/sessions/*.md` (~120 fragments + README.md)
- Modify → stub: `lex/test_project/test-plan/test-clusters.md`, `test-writing-plan.md`, `progress/session-log.md`
- Regenerate: `lex/test_project/test-plan/progress/dashboard.md`

This task has judgment steps — it is the one place the migration is *reviewed*, not
just executed. Known wrinkles to resolve (found during planning):

- `test-writing-plan.md` has a `## Cluster 13 — Process Admin (new — opens here)`
  block, but cluster 13 in `test-clusters.md` is **Export Endpoint** (`exports`).
  During review, decide where the Process-Admin block belongs (likely
  `clusters/README.md` pending-decisions if it never opened, or its own numbered
  cluster if tests exist) — do not let it silently land in `13-exports/batches.md`.
- Old dashboard rows that are scenario-level (e.g. `5.11. History fallback-snapshot`)
  or sub-cluster rows with rich notes: port their status/counts/notes into the owning
  cluster's `allocation.yaml` letters by hand.

- [ ] **Step 1: Dry-run the splitter**

Run: `python .github/scripts/test_plan_split.py --plan-dir lex/test_project/test-plan`
Expected: `fact audit: OK` and `dry run — re-run with --apply to write.`
If the fact audit fails: the listed facts are regex-extraction gaps (e.g. an unusual
heading format). Fix the regex in `test_plan_split.py` (add a matching unit test in
`test_test_plan_split.py` reproducing the exact heading), re-run until OK.

- [ ] **Step 2: Apply**

Run: `python .github/scripts/test_plan_split.py --plan-dir lex/test_project/test-plan --apply`
Expected: `applied.`

- [ ] **Step 3: Review + reconcile allocation.yaml (the human/agent judgment pass)**

For each `clusters/NN-<slug>/allocation.yaml`:
1. Open `progress/dashboard-pre-migration.md`, find every row for cluster NN, and
   port `Passing / Expected Failures / skip` counts, statuses, and terse notes into
   the matching letters (replacing the `seeded by test_plan_split.py — verify` note).
2. Cross-check `max_scenario` against the highest scenario ID in the cluster's
   `cluster.md` + `batches.md`; bump if the seed missed a range format.
3. Resolve the Cluster-13 Process-Admin wrinkle (see above).

Then delete the review scratch file:

```bash
rm lex/test_project/test-plan/progress/dashboard-pre-migration.md
```

- [ ] **Step 4: Write `progress/sessions/README.md`**

```markdown
# Session Fragments

> **What this is:** the chronological narrative of test-plan work — one file per
> session/PR (the old `session-log.md` table, exploded; see the restructure spec).
>
> **To add a session:** create `YYYY-MM-DD-<short-slug>.md` (slug = batch id or
> branch name — never a counter). Front-matter: `date`, `clusters`, `tests_added`,
> `suite_tally`. Body: short prose leading with the batch touched — link the
> batch in `../../clusters/NN-<slug>/batches.md` rather than restating it.
> Adding a file never conflicts with another PR — that is the point.
```

- [ ] **Step 5: Replace the three monoliths with pointer stubs**

`lex/test_project/test-plan/test-clusters.md`:

```markdown
# Moved — Test Clusters Are Now Sharded

This file was retired on 2026-07-07 (restructure spec:
`docs/superpowers/specs/2026-07-07-test-plan-scalability-restructure-design.md`).

- Philosophy + Golden Rule → [`testing-philosophy.md`](testing-philosophy.md)
- Cluster N definition → `clusters/NN-<slug>/cluster.md`
- Do **not** add content here — the PR gate no longer accepts edits to this file.
```

`lex/test_project/test-plan/test-writing-plan.md`:

```markdown
# Moved — Batch Allocation Is Now Per-Cluster

Retired 2026-07-07 (see the restructure spec).

- Batch history for cluster N → `clusters/NN-<slug>/batches.md`
- Machine allocation state (next letter / max scenario) → `clusters/NN-<slug>/allocation.yaml`
- Conventions, LATER backlog, pending decisions → [`clusters/README.md`](clusters/README.md)
```

`lex/test_project/test-plan/progress/session-log.md`:

```markdown
# Moved — Session Log Is Now Fragments

Retired 2026-07-07. One file per session under [`sessions/`](sessions/) —
see [`sessions/README.md`](sessions/README.md) for the format.
```

- [ ] **Step 6: Build the dashboard + run both consistency gates**

```bash
python .github/scripts/test_plan_aggregates.py build
python .github/scripts/test_plan_aggregates.py check
python .github/scripts/test_plan_aggregates.py validate
```

Expected: `check` → OK. `validate` → likely a handful of findings on first run
(pre-existing drift the monoliths were hiding: letters missing from YAML, scenario
IDs above the seeded max). **Each finding is reconciled by fixing the YAML** (bump
`max_scenario`, add the missing letter with `status: complete`) — never by touching
test files. Re-run until OK. If a finding reveals a *genuine* duplicate scenario ID
in two test files, record it in `known-bugs.md` as a plan-consistency bug and fix the
lower-value docstring.

- [ ] **Step 7: Commit the migrated tree**

```bash
git add lex/test_project/test-plan/
git commit -m "refactor(test-plan): shard monoliths into per-cluster dirs + session fragments + generated dashboard"
```

---

### Task 5: New PR-shape rules in `copilot_validate_pr_shape.py`

**Files:**
- Modify: `.github/scripts/copilot_validate_pr_shape.py`
- Modify: `.github/scripts/tests/test_copilot_validate_pr_shape.py`

- [ ] **Step 1: Add failing tests**

Add to `test_copilot_validate_pr_shape.py` (match the file's existing fixture style —
it builds `PRFile` lists and calls `validate_pr_shape`; extend `PRFile` construction
with the new `status` kwarg):

```python
class TestShardedPlanRules:
    def _plan_dir(self, tmp_path):
        d = tmp_path / "test-plan" / "clusters" / "07-calculations"
        d.mkdir(parents=True)
        d.joinpath("allocation.yaml").write_text(
            "cluster: 7\nslug: calculations\ntitle: Calc\n"
            "test_dirs: [calculations, calculation_logging]\n"
            "max_scenario: 10\nletters: {a: {status: complete}}\n"
        )
        return tmp_path / "test-plan"

    def _files(self, *paths_status):
        return [PRFile(path=p, additions=5, deletions=0, status=s) for p, s in paths_status]

    def test_valid_sharded_pr(self, tmp_path):
        files = self._files(
            ("lex/test_project/tests/calculations/test_7b_new.py", "added"),
            ("lex/test_project/test-plan/clusters/07-calculations/allocation.yaml", "modified"),
            ("lex/test_project/test-plan/clusters/07-calculations/batches.md", "modified"),
            ("lex/test_project/test-plan/progress/sessions/2026-07-07-batch-7b.md", "added"),
            ("lex/test_project/test-plan/progress/dashboard.md", "modified"),
        )
        r = validate_pr_shape(mode="regression", files=files, pr_body="Fixes #1",
                              plan_dir=self._plan_dir(tmp_path))
        assert r.ok, r.errors

    def test_missing_cluster_shard_edit(self, tmp_path):
        files = self._files(
            ("lex/test_project/tests/calculations/test_7b_new.py", "added"),
            ("lex/test_project/test-plan/progress/sessions/2026-07-07-x.md", "added"),
        )
        r = validate_pr_shape(mode="regression", files=files, pr_body="Fixes #1",
                              plan_dir=self._plan_dir(tmp_path))
        assert not r.ok
        assert any("07-calculations" in e for e in r.errors)

    def test_missing_session_fragment(self, tmp_path):
        files = self._files(
            ("lex/test_project/tests/calculations/test_7b_new.py", "added"),
            ("lex/test_project/test-plan/clusters/07-calculations/allocation.yaml", "modified"),
        )
        r = validate_pr_shape(mode="regression", files=files, pr_body="Fixes #1",
                              plan_dir=self._plan_dir(tmp_path))
        assert not r.ok
        assert any("sessions/" in e for e in r.errors)

    def test_test_dirs_alias_maps_to_owning_cluster(self, tmp_path):
        files = self._files(
            ("lex/test_project/tests/calculation_logging/test_7c_log.py", "added"),
            ("lex/test_project/test-plan/clusters/07-calculations/allocation.yaml", "modified"),
            ("lex/test_project/test-plan/progress/sessions/2026-07-07-y.md", "added"),
        )
        r = validate_pr_shape(mode="regression", files=files, pr_body="Fixes #1",
                              plan_dir=self._plan_dir(tmp_path))
        assert r.ok, r.errors

    def test_edit_to_retired_monolith_rejected(self, tmp_path):
        files = self._files(
            ("lex/test_project/tests/calculations/test_7b_new.py", "added"),
            ("lex/test_project/test-plan/clusters/07-calculations/allocation.yaml", "modified"),
            ("lex/test_project/test-plan/progress/sessions/2026-07-07-z.md", "added"),
            ("lex/test_project/test-plan/test-clusters.md", "modified"),
        )
        r = validate_pr_shape(mode="regression", files=files, pr_body="Fixes #1",
                              plan_dir=self._plan_dir(tmp_path))
        assert not r.ok
        assert any("retired" in e for e in r.errors)
```

Also update every pre-existing test in the file that asserts the old rules
("test-clusters.md was not modified" / "session-log.md was not appended") — those
assertions are inverted now; the old-rule tests are deleted, and existing
happy-path fixtures gain the shard + fragment files.

- [ ] **Step 2: Run to verify the new class fails**

Run: `python -m pytest .github/scripts/tests/test_copilot_validate_pr_shape.py -v`
Expected: FAIL (new class errors; old-rule tests fail after their update until Step 3)

- [ ] **Step 3: Implement**

In `copilot_validate_pr_shape.py`:

```python
# PRFile gains a status field (files.json already carries it — see the gate yml):
@dataclass
class PRFile:
    path: str
    additions: int
    deletions: int
    status: str = ""   # "added" | "modified" | "removed" | ...

RETIRED_MONOLITHS = (
    "lex/test_project/test-plan/test-clusters.md",
    "lex/test_project/test-plan/test-writing-plan.md",
    "lex/test_project/test-plan/progress/session-log.md",
)
SESSION_FRAGMENT_RE = re.compile(
    r"^lex/test_project/test-plan/progress/sessions/\d{4}-\d{2}-\d{2}-[A-Za-z0-9._-]+\.md$"
)
CLUSTER_SHARD_PREFIX = "lex/test_project/test-plan/clusters/"


def _slug_to_shard_dir(plan_dir: Path) -> dict[str, str]:
    """Map every claimed test-dir slug -> its clusters/NN-<slug>/ dir name,
    by reading allocation.yaml files (test_dirs aliases included)."""
    out: dict[str, str] = {}
    for yml in sorted((plan_dir / "clusters").glob("*/allocation.yaml")):
        data = yaml.safe_load(yml.read_text())
        for d in data.get("test_dirs") or [data["slug"]]:
            out[d] = yml.parent.name
    return out
```

Replace rules 2 and 3 inside `validate_pr_shape` (which gains a
`plan_dir: Path | None = None` kwarg, defaulting to
`Path("lex/test_project/test-plan")` when None):

```python
    # 2. Each added test file's cluster shard was edited.
    shard_by_slug = _slug_to_shard_dir(plan_dir)
    for p in test_files:
        slug = p.split("/")[3]  # lex/test_project/tests/<slug>/...
        shard = shard_by_slug.get(slug)
        if shard is None:
            errors.append(
                f"test folder {slug!r} is not claimed by any cluster's "
                "allocation.yaml (test_dirs)"
            )
        elif not any(x.startswith(f"{CLUSTER_SHARD_PREFIX}{shard}/") for x in paths):
            errors.append(
                f"`{CLUSTER_SHARD_PREFIX}{shard}/` was not updated for new test `{p}` "
                "(allocation.yaml + batches.md at minimum)"
            )

    # 3. Exactly ≥1 NEW session fragment.
    fragments = [
        f for f in files
        if SESSION_FRAGMENT_RE.match(f.path) and f.status == "added"
    ]
    if not fragments:
        errors.append(
            "no new session fragment added under "
            "`lex/test_project/test-plan/progress/sessions/` "
            "(named YYYY-MM-DD-<slug>.md)"
        )

    # 3b. Retired monoliths are frozen.
    for p in paths:
        if p in RETIRED_MONOLITHS:
            errors.append(f"`{p}` is retired (pointer stub) — edit the sharded plan instead")
```

Add `import yaml` at the top and `--plan-dir` to `_cli()` (type `Path`, default
`Path("lex/test_project/test-plan")`), passing it through to `validate_pr_shape`,
and include `status=r.get("status", "")` when building `PRFile`s from JSON.

- [ ] **Step 4: Run the full file**

Run: `python -m pytest .github/scripts/tests/test_copilot_validate_pr_shape.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add .github/scripts/copilot_validate_pr_shape.py .github/scripts/tests/test_copilot_validate_pr_shape.py
git commit -m "feat(gate): sharded-plan PR-shape rules — cluster shard edit + session fragment, monoliths frozen"
```

---

### Task 6: Wire the gates into `copilot_pr_gate.yml`

**Files:**
- Modify: `.github/workflows/copilot_pr_gate.yml`

- [ ] **Step 1: Add two steps immediately after the "Validate PR shape" step** (the one
  invoking `copilot_validate_pr_shape.py` around line 206), keeping its indentation:

```yaml
      - name: Test-plan dashboard freshness
        run: python .github/scripts/test_plan_aggregates.py check

      - name: Test-plan allocation consistency
        run: python .github/scripts/test_plan_aggregates.py validate
```

- [ ] **Step 2: Ensure PyYAML is installed before those steps.** Find the dependency
  step in the same job; the gate installs the lex package (which brings PyYAML — it
  is in the framework requirements). Verify with:

Run: `grep -n "pip install" .github/workflows/copilot_pr_gate.yml`
If the shape-check job does *not* install the package, add `pip install pyyaml` to
that job's setup step.

- [ ] **Step 3: Validate the workflow syntax**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/copilot_pr_gate.yml'))"`
Expected: no output (valid YAML)

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/copilot_pr_gate.yml
git commit -m "ci(gate): run test-plan freshness + allocation consistency checks on Copilot PRs"
```

---

### Task 7: Re-point the cloud prompt (`copilot_assemble_prompt.py`)

**Files:**
- Modify: `.github/scripts/copilot_assemble_prompt.py`
- Modify: `.github/scripts/tests/test_copilot_assemble_prompt.py`

- [ ] **Step 1: Update the existence checks** (currently `for required in
  ("test-clusters.md", "test-writing-plan.md")`):

```python
    for required in ("testing-philosophy.md", "clusters", "progress/sessions"):
        if not (test_plan_dir / required).exists():
            raise FileNotFoundError(f"required test-plan path missing: {test_plan_dir / required}")
```

- [ ] **Step 2: Replace the "Cluster catalogue" and "Writing-plan rules" prompt sections**

```markdown
## Cluster catalogue

**The plan is sharded: read `lex/test_project/test-plan/clusters/<NN>-<slug>/` for your target cluster** — `cluster.md` owns scope + scenarios, `batches.md` owns batch history, `allocation.yaml` owns the machine allocation state. `lex/test_project/test-plan/index.md` has the cluster table; `testing-philosophy.md` has the rules. Do NOT read or edit `test-clusters.md` / `test-writing-plan.md` — they are retired pointer stubs and the gate rejects edits to them.

## Allocation rules (file naming, scenario IDs, letters)

**Allocate from `clusters/<NN>-<slug>/allocation.yaml`:** your scenario range starts at `max_scenario + 1`; your sub-cluster letter is the next lowercase letter not present in `letters:`. Bump `max_scenario` and add your letter entry (title, scenarios, status, tests counts, note) in the same PR — the gate cross-checks your test files against the YAML. Cross-cluster conventions (LATER backlog, pending decisions) live in `clusters/README.md`.
```

- [ ] **Step 3: Rewrite the "Required deliverables in the PR" list**

```markdown
## Required deliverables in the PR

- One or more new test files under `lex/test_project/tests/<slug>/test_<Nx>_<short>.py` — one per cluster involved (regression mode may have several; bug-repro and fix-and-test have exactly one).
- For **each** touched cluster `NN-<slug>` under `lex/test_project/test-plan/clusters/`: `allocation.yaml` updated (letter entry + `max_scenario`), `batches.md` batch block appended, and `cluster.md` scenario table extended if you defined new scenarios.
- One **new** session fragment `lex/test_project/test-plan/progress/sessions/YYYY-MM-DD-<slug>.md` (front-matter: date/clusters/tests_added/suite_tally; body: short prose linking the batch — never restate the batch table).
- Regenerate the dashboard: run `python .github/scripts/test_plan_aggregates.py build` and commit `progress/dashboard.md` — never hand-edit it.
- Mode B/C only: one new BUG-NNN row in `lex/test_project/test-plan/known-bugs.md`.
- New-cluster placement only: create `clusters/NN-<slug>/` (cluster.md + batches.md + allocation.yaml), add the index.md table row, register in `.github/scripts/showcase_clusters.py`, update default selectors in `pip_publish.yml` + `showcase_tests.yml`.
- Touch nothing outside the allowed file set, except the source fix in mode `fix-and-test`.
```

Also update cluster-routing rule 1/2 text: "If the letter is taken, advance to the
next free letter per `test-writing-plan.md`" → "per that cluster's `allocation.yaml`";
rule 5's "add a `test-clusters.md` entry" → "create the `clusters/NN-<slug>/` trio".

- [ ] **Step 4: Update `test_copilot_assemble_prompt.py`** — its fixtures create a fake
  `test_plan_dir`; change them to create `testing-philosophy.md`, `clusters/` (with one
  `01-init/allocation.yaml`) and `progress/sessions/` instead of the two monoliths, and
  update assertions that grep the prompt body for the retired paths to grep for
  `clusters/` and `allocation.yaml` instead.

- [ ] **Step 5: Run**

Run: `python -m pytest .github/scripts/tests/test_copilot_assemble_prompt.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add .github/scripts/copilot_assemble_prompt.py .github/scripts/tests/test_copilot_assemble_prompt.py
git commit -m "feat(bot): cloud prompt reads the sharded plan — per-cluster dir + allocation.yaml"
```

---

### Task 8: Local agent surfaces + cross-links

**Files:**
- Modify: `.claude/skills/lex-testing/SKILL.md`
- Modify: `.github/instructions/testing.instructions.md`
- Modify: `AGENTS.md`
- Modify: `lex/test_project/test-plan/progress/conventions.md`
- Modify: `lex/test_project/test-plan/index.md`, `lex/test_project/test-plan/progress.md`
- Modify: `docs/ci-cd/copilot-test-bot.md`

- [ ] **Step 1: `SKILL.md`** — rewrite the path-bearing steps:
  - Step 1 reading list → `index.md`, `testing-philosophy.md`,
    `clusters/<NN>-<slug>/` (cluster.md + batches.md + allocation.yaml),
    `known-bugs.md`. Remove the monolith paths.
  - Step 3 → "Read `allocation.yaml`: next letter = first lowercase letter absent
    from `letters:`; scenario range starts at `max_scenario + 1`. In-flight check =
    letters with `status: planned|in-flight` and their `batches.md` rows."
  - Step 7 (DoD) → per-cluster shard edits (`allocation.yaml`, `batches.md`,
    `cluster.md` when scenarios were defined), one **new** session fragment under
    `progress/sessions/` (link the batch, don't restate it), then
    `python .github/scripts/test_plan_aggregates.py build` and commit the dashboard.
  - "Never" list: add "edit `test-clusters.md` / `test-writing-plan.md` /
    `session-log.md` — they are retired stubs" and "hand-edit
    `progress/dashboard.md`".
  - Keep the Step-2 surface-enumeration wording INTACT (parity test pins it).

- [ ] **Step 2: `testing.instructions.md` + `AGENTS.md`** — same substitutions: every
  reference to `test-clusters.md` → `clusters/NN-<slug>/cluster.md` (or
  `testing-philosophy.md` when the reference is to the philosophy/red-flags),
  `test-writing-plan.md` → `clusters/NN-<slug>/allocation.yaml` + `batches.md`,
  `session-log.md` append → new fragment under `progress/sessions/`, dashboard bump →
  `test_plan_aggregates.py build`. Keep the execution-path clause wording intact in
  both (parity test).

- [ ] **Step 3: `conventions.md`** — fix its internal links (`../test-clusters.md#testing-philosophy`
  → `../testing-philosophy.md`; dashboard/session-log references → new paths), and add
  the single-home table:

```markdown
## Where to Write What (single home per fact)

| Fact | Its ONLY home |
|---|---|
| Scenario intent + definition | `clusters/NN-<slug>/cluster.md` |
| Batch write-up (files, classes, fixtures, results) | `clusters/NN-<slug>/batches.md` |
| Allocation state (letters, max scenario, status, counts) | `clusters/NN-<slug>/allocation.yaml` |
| Session narrative | one NEW file in `progress/sessions/` — link the batch, never restate it |
| Bug ledger | `known-bugs.md` |
| Dashboard | ⚙ generated — run `python .github/scripts/test_plan_aggregates.py build` |

Everything else links. If you are about to write the same fact in a second place, stop
and link instead.
```

- [ ] **Step 4: `index.md` + `progress.md` + `docs/ci-cd/copilot-test-bot.md`** — update
  navigation links (test-clusters/test-writing-plan/session-log → new paths). In
  `index.md`, point the "Test Project Structure" and cluster-table links at
  `clusters/NN-<slug>/cluster.md`, and leave the Golden-Rule block untouched
  (`copilot_assemble_prompt.py` extracts it by anchor).

- [ ] **Step 5: Run the drift guard + script suites**

```bash
python -m pytest .github/scripts/tests/test_prompt_surface_parity.py -v
python -m pytest .github/scripts/tests/ -v
```

Expected: PASS. The parity test in particular proves the four agent surfaces still
carry the canonical surface-enumeration rule after the path rewrites.

- [ ] **Step 6: Commit**

```bash
git add .claude/skills/lex-testing/SKILL.md .github/instructions/testing.instructions.md \
        AGENTS.md lex/test_project/test-plan/ docs/ci-cd/copilot-test-bot.md
git commit -m "docs(test-plan): re-point all agent surfaces at the sharded plan; single-home-per-fact rule"
```

---

### Task 9: End-to-end verification

- [ ] **Step 1: Full script test suite**

Run: `python -m pytest .github/scripts/tests/ -v`
Expected: PASS — all files including the two new ones and the parity guard.

- [ ] **Step 2: Aggregates gates green on the real tree**

```bash
python .github/scripts/test_plan_aggregates.py check
python .github/scripts/test_plan_aggregates.py validate
```

Expected: both `OK`.

- [ ] **Step 3: Dry-run the prompt assembler against the real plan** (proves the cloud
  path works before a real dispatch):

```bash
python - <<'PY'
import json, sys
sys.path.insert(0, ".github/scripts")
from pathlib import Path
from copilot_assemble_prompt import assemble_prompt, IssueInput, Mode
body = assemble_prompt(
    IssueInput(number=1, title="smoke", behaviour="x", reproducer="", cluster_hint="7",
               files=[], mode=Mode.REGRESSION),
    test_plan_dir=Path("lex/test_project/test-plan"),
)
print(f"{len(body.encode())} bytes"); assert "allocation.yaml" in body
PY
```

Expected: a byte count well under 65536 and no assertion error. (Check `IssueInput`'s
actual constructor signature in the module and adjust the smoke call if it differs.)

- [ ] **Step 4: Sanity-check the test suite still collects** (plan files only — imports
  unaffected, this is a belt-and-braces check):

Run: `python -m lex pytest lex/test_project/tests/init -v --collect-only -q | tail -5`
Expected: normal collection output, no errors.

- [ ] **Step 5: Final commit if anything was fixed, then hand off**

The branch is ready for PR against `lex-app-v2`. Note in the PR body: in-flight
Copilot PRs opened against the old plan shape will fail the new gate with pointer-stub
messages and must be re-dispatched.

---

## Self-review notes

- **Spec coverage:** §3 layout → Tasks 3–4; §4 allocation.yaml + gate cross-check →
  Tasks 1–2, 4, 5; §5 aggregates → Tasks 1–2, 6; §6 consumer table → Tasks 5–8;
  §7 migration + audit → Tasks 3–4; §7.5 docs-sync check → the `docs/.docs-sync.yml`
  manifest lists only `docs/` paths, so `lex/test_project/test-plan/` is unaffected
  (verified during planning — no task needed).
- **Known judgment points** are concentrated in Task 4 Step 3 (Cluster-13 wrinkle,
  count porting) — deliberately not automated.
- **Regex fragility** in the splitter is bounded by the fact audit: any heading the
  regexes miss shows up as a missing fact and hard-fails the dry run (Task 4 Step 1
  documents the fix loop).
