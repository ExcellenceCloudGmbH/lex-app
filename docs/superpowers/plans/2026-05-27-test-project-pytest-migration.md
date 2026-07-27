# Test-Project Migration to `pytest` + `lex_test_config.yaml` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate `lex/test_project/tests/` (138 test files) from `lex test` (Django runner) to `python -m lex pytest` driven by a new `lex_test_config.yaml` at the repo root, in three sequenced PRs (prep → CI flip → docs).

**Architecture:** Three-PR feature-flag cutover. **PR-A** lands the YAML and adds `pytestmark = pytest.mark.<group>` to every test file — inert under `lex test` because Django's runner ignores module-level marker attributes. **PR-B** swaps the two CI surfaces that invoke `lex test` (`copilot_pr_gate.yml` modes A/B/C + `run_showcase_suite.py`) over to `lex pytest`, including translating dotted Django labels to pytest nodeids and updating output-parsing regexes. **PR-C** updates the test-plan docs (`conventions.md`, `index.md`, `test-writing-plan.md`, `CLAUDE.md`) — examples only; every rule, gate, and philosophy section stays byte-identical.

**Tech Stack:** Python 3.11+, pytest (already a dep of `lex/tools/test_groups.py`), Django test runner (current baseline), `lex` CLI (`lex/bin/lex.py`), GitHub Actions, `coverage.py`.

**Spec:** [`docs/superpowers/specs/2026-05-27-test-project-pytest-migration-design.md`](../specs/2026-05-27-test-project-pytest-migration-design.md)

---

## File Inventory

| Action | Path | Responsibility |
|---|---|---|
| Create | `lex_test_config.yaml` | Single source of truth for `lex pytest` — entrypoint, 16 groups, placeholder report/email blocks |
| Create | `scripts/add_pytest_markers.py` | One-shot, idempotent helper that inserts `pytestmark` lines into all test files. Deleted after PR-A merges (one-shot tool) |
| Modify | `lex/test_project/tests/<group>/test_*.py` (138 files) | Two-line marker addition each; no behavior change |
| Modify | `.github/workflows/copilot_pr_gate.yml` | Modes A/B/C: `lex test` → `lex pytest`; Mode-B failure-detection regex updated; stale comment deleted |
| Modify | `.github/scripts/run_showcase_suite.py` | `_run_cluster`: label → path/nodeid translation; output-parsing regex updates |
| Modify | `lex/test_project/test-plan/progress/conventions.md` | §"How to Run Tests" code blocks + two additive sentences (rules unchanged) |
| Modify | `lex/test_project/test-plan/index.md` | One additive sentence under "Test Project Structure" |
| Modify | `lex/test_project/test-plan/test-writing-plan.md` | One footnote at the bottom |
| Modify | `CLAUDE.md` | Single line (358) — only the `lex.test_project` invocation; framework-tree lines untouched |

---

## PR-A — Prep PR (YAML + markers, inert)

### Task A1: Create `lex_test_config.yaml`

**Files:**
- Create: `lex_test_config.yaml`

- [ ] **Step 1: Write the YAML at repo root**

Create `/home/syscall/Documents/lex/lex_test_config.yaml` with exactly this content:

```yaml
# Lex test-group configuration — single source of truth for `python -m lex pytest`.
# Loaded by lex/tools/test_groups.py at project_root.

tests_entrypoint: lex/test_project/tests

# Recipients are intentionally empty for now. The reporting/email surface is
# implemented in test_groups.py but not yet turned on. When stakeholders are
# decided, populate this list and per-group receivers below.
receivers: []

report:
  output_dir: reports

email:
  from_email: "noreply@example.com"
  from_name: "Lex Reports"
  reply_to: "noreply@example.com"
  subject_prefix: "Lex test report"

# Group names match the cluster folder names under lex/test_project/tests/.
# One test module → one marker via module-level `pytestmark = pytest.mark.<group>`.
# Sub-cluster selection (e.g. 7k, 1o, 6n) uses `-k` against the existing
# test_N_M_* method naming, not finer-grained markers.
groups:
  - { name: init,                description: "Cluster 1 — Project bootstrap, lex setup, lex Init, Keycloak sync, seed data." }
  - { name: crud_api,            description: "Cluster 2 — CRUD via REST API (POST/GET/PATCH/DELETE)." }
  - { name: validation_hooks,    description: "Cluster 3 — pre_validation / post_validation hooks." }
  - { name: permissions,         description: "Cluster 4 — Field- and action-level access control." }
  - { name: history,             description: "Cluster 5 — History rows, bitemporal chaining, MetaHistory." }
  - { name: audit_logging,       description: "Cluster 6 — AuditLogMixin lifecycle, audit finalization." }
  - { name: calculations,        description: "Cluster 7 — Calculation state machine, atomic/non-atomic, parent→child chains." }
  - { name: calculation_logging, description: "Cluster 7 logging surface — calculation-log tree, builder API." }
  - { name: celery_async,        description: "Cluster 8 — Celery dispatch, sync fallback, FireAndForget / WaitForTasks." }
  - { name: signals_ws,          description: "Cluster 9 — Signals, WebSocket consumers, active-state store." }
  - { name: api_layer,           description: "Cluster 10 — REST endpoints (history, bulk, files, SharePoint, schema)." }
  - { name: stress,              description: "Cluster 11 — ~20k-row workload tests. Opt-in via -m stress." }
  - { name: serializers,         description: "Cluster 12 — Serializer contract: JSON shape, type round-trip, reserved scopes." }
  - { name: exports,             description: "Cluster 13 — Export endpoint, AG Grid flat/grouped, FK display names." }
  - { name: queries,             description: "Cluster 14 — AG Grid query endpoint, filter/sort/group models." }
  - { name: journeys,            description: "End-to-end multi-cluster user journeys." }
```

- [ ] **Step 2: Verify the file loads via test_groups.py**

Run:

```bash
cd /home/syscall/Documents/lex
python -c "from lex.tools.test_groups import load_config_from_disk; from pathlib import Path; cfg = load_config_from_disk(Path('.')); print(f'OK: entrypoint={cfg.tests_entrypoint}, groups={len(cfg.groups)}')"
```

Expected output:

```
OK: entrypoint=lex/test_project/tests, groups=16
```

If `LexTestConfigError` is raised, read the error message — it names the offending field. Fix the YAML and re-run until it loads cleanly.

- [ ] **Step 3: Commit**

```bash
git add lex_test_config.yaml
git commit -m "Add lex_test_config.yaml with 16 cluster groups

Lays the YAML schema expected by lex/tools/test_groups.py.
Inert under the existing lex test runner; consumed by lex pytest
once PR-B flips the CI gate.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task A2: Write the one-shot marker-insertion script

**Files:**
- Create: `scripts/add_pytest_markers.py`

- [ ] **Step 1: Write the script**

Create `/home/syscall/Documents/lex/scripts/add_pytest_markers.py` with:

```python
#!/usr/bin/env python3
"""
One-shot, idempotent: insert `pytestmark = pytest.mark.<group>` into every
test module under lex/test_project/tests/<group>/test_*.py.

The group is the parent directory name. Insertion point is right after the
last top-level `import` / `from` statement in the file. If the file already
has a `pytestmark = pytest.mark.` line, it is left untouched (idempotent).

Run from the repo root:
    python scripts/add_pytest_markers.py

This script is deleted after PR-A merges — it is a one-shot tool.
"""
from __future__ import annotations

import ast
import pathlib
import sys

TESTS_ROOT = pathlib.Path("lex/test_project/tests")
MARKER_PROBE = "pytestmark = pytest.mark."


def find_insertion_line(source: str) -> int:
    """Return the 1-based line number AFTER the last top-level import."""
    tree = ast.parse(source)
    last = 0
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            # node.end_lineno is the last line of the statement
            last = max(last, node.end_lineno or node.lineno)
    return last


def already_marked(source: str) -> bool:
    return MARKER_PROBE in source


def needs_pytest_import(source: str) -> bool:
    # Look for `import pytest` or `import pytest as ...` at module level.
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return True
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "pytest":
                    return False
    return True


def process(path: pathlib.Path) -> str:
    """Return one of: 'added', 'already', 'error:<msg>'."""
    group = path.parent.name
    source = path.read_text()
    if already_marked(source):
        return "already"
    try:
        insert_after = find_insertion_line(source)
    except SyntaxError as exc:
        return f"error:syntax:{exc}"
    if insert_after == 0:
        return "error:no_imports_found"

    lines = source.splitlines(keepends=True)
    # `insert_after` is 1-based; convert to 0-based index for the insertion point.
    idx = insert_after  # we want to insert AFTER this line

    # Build the block to insert.
    pieces: list[str] = []
    pieces.append("\n")  # blank line after last import
    if needs_pytest_import(source):
        pieces.append("import pytest\n")
        pieces.append("\n")
    pieces.append(f"pytestmark = pytest.mark.{group}\n")

    new_source = "".join(lines[:idx] + pieces + lines[idx:])
    path.write_text(new_source)
    return "added"


def main() -> int:
    if not TESTS_ROOT.is_dir():
        print(f"ERROR: {TESTS_ROOT} does not exist (run from repo root)", file=sys.stderr)
        return 2

    counts = {"added": 0, "already": 0, "errors": 0}
    errors: list[tuple[pathlib.Path, str]] = []

    for path in sorted(TESTS_ROOT.rglob("test_*.py")):
        result = process(path)
        if result == "added":
            counts["added"] += 1
            print(f"  added: {path}")
        elif result == "already":
            counts["already"] += 1
        else:
            counts["errors"] += 1
            errors.append((path, result))

    print()
    print(f"Summary: added={counts['added']}, already_marked={counts['already']}, errors={counts['errors']}")
    if errors:
        print("\nErrors:")
        for path, msg in errors:
            print(f"  {path}: {msg}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Dry-run sanity check on one file**

Pick a single file to spot-check the algorithm before bulk-running.

Run:

```bash
cd /home/syscall/Documents/lex
cp lex/test_project/tests/crud_api/test_2a_create.py /tmp/spotcheck_before.py
python scripts/add_pytest_markers.py 2>&1 | head -3
diff /tmp/spotcheck_before.py lex/test_project/tests/crud_api/test_2a_create.py
```

Expected diff output:

```
> 
> import pytest
> 
> pytestmark = pytest.mark.crud_api
```

If the diff shows lines inserted at a place other than directly after the last import, the algorithm has a bug — investigate `find_insertion_line` before continuing.

**Do NOT commit yet — Task A3 verifies the bulk run is complete and clean.**

---

### Task A3: Bulk-run the marker insertion, verify, commit

**Files:**
- Modify: `lex/test_project/tests/<group>/test_*.py` (138 files)

- [ ] **Step 1: Run the script (it's idempotent; rerunning after the A2 spot-check is safe)**

```bash
cd /home/syscall/Documents/lex
python scripts/add_pytest_markers.py | tail -20
```

Expected last lines (numbers approximate, must equal 138 total processed):

```
Summary: added=137, already_marked=1, errors=0
```

(The `already_marked=1` reflects the file modified during the A2 spot-check; if you reverted that one, you'll see `added=138, already_marked=0`.)

- [ ] **Step 2: Verify every test file has the marker**

```bash
cd /home/syscall/Documents/lex
find lex/test_project/tests -name 'test_*.py' \
  -exec grep -L 'pytestmark = pytest.mark.' {} +
```

Expected output: **empty** (no files lacking the marker).

If any file is listed, open it and add the marker manually using the same pattern.

- [ ] **Step 3: Verify each file's marker matches its parent folder**

```bash
cd /home/syscall/Documents/lex
python - <<'PY'
import pathlib, re
base = pathlib.Path("lex/test_project/tests")
bad = []
for p in base.rglob("test_*.py"):
    group = p.parent.name
    expected = f"pytestmark = pytest.mark.{group}"
    if expected not in p.read_text():
        bad.append(str(p))
if bad:
    print("MISMATCHED markers in:")
    for b in bad:
        print(f"  {b}")
    raise SystemExit(1)
print("OK — every test file's marker matches its folder.")
PY
```

Expected output:

```
OK — every test file's marker matches its folder.
```

- [ ] **Step 4: Confirm the existing runner is unchanged (baseline)**

This is the key gate proving PR-A is inert. Capture the baseline test count and a checksum of `dashboard.md`-relevant metrics by running the existing runner.

```bash
cd /home/syscall/Documents/lex
source ~/LUND_IT/ArmiraCashflowDB/.venv/bin/activate
DJANGO_SETTINGS_MODULE=lex_app.settings DATABASE_DEPLOYMENT_TARGET=default CELERY_ACTIVE=False \
  lex test lex.test_project.tests --noinput 2>&1 | tail -20
```

Expected: the final line reads something like `OK` (or `OK (expected failures=N, skipped=M)`), and `Ran <N> tests in <T>s` immediately above. **N must match the pre-PR-A baseline.** Compare against `git stash && lex test ... && git stash pop` if you want a hard check, but a known-good prior CI green run is sufficient evidence.

If the count differs by more than ±0, **stop** — the marker addition has somehow changed test discovery. Inspect with `git diff lex/test_project/tests/` and revert any non-marker change.

- [ ] **Step 5: Verify `lex pytest --collect-only` discovers the same suite**

```bash
cd /home/syscall/Documents/lex
DJANGO_SETTINGS_MODULE=lex_app.settings DATABASE_DEPLOYMENT_TARGET=default CELERY_ACTIVE=False \
  python -m lex pytest -m "not stress" --collect-only -q 2>&1 | tail -5
```

Expected: a final summary line like `<N> tests collected in <T>s`. The N here should be **≤** the `lex test` count from Step 4 (equal if no stress tests exist yet; slightly fewer if any stress tests were collected by Django but excluded by `-m "not stress"` here).

If pytest reports collection errors (e.g. `ImportError`, marker errors), **stop** — read the error. The most likely cause is an unmarked test file (caught earlier by Step 2) or a YAML group name mismatch (the plugin will name the offending group).

- [ ] **Step 6: Delete the one-shot script**

```bash
cd /home/syscall/Documents/lex
rm scripts/add_pytest_markers.py
# If scripts/ is now empty, leave it — other future scripts may land there.
```

- [ ] **Step 7: Commit**

```bash
cd /home/syscall/Documents/lex
git add lex/test_project/tests
git rm scripts/add_pytest_markers.py 2>/dev/null || true
git commit -m "Add pytestmark to all test_project test modules

Module-level pytestmark = pytest.mark.<cluster_folder_name> on every
test_*.py under lex/test_project/tests/<group>/. Inert under the
existing Django runner; consumed by lex pytest once PR-B flips CI.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task A4: Open PR-A

- [ ] **Step 1: Push the branch and open PR**

```bash
cd /home/syscall/Documents/lex
git push -u origin HEAD
gh pr create --title "Test-project pytest migration — PR-A (prep, inert)" --body "$(cat <<'EOF'
## Summary
- Adds `lex_test_config.yaml` at the repo root with 16 cluster groups, matching the folder structure under `lex/test_project/tests/`.
- Adds module-level `pytestmark = pytest.mark.<group>` to every test file (138 modules touched, 2 inserted lines each).

This PR is **inert** under the existing `lex test` runner — Django ignores `pytestmark` attributes. Spec: [docs/superpowers/specs/2026-05-27-test-project-pytest-migration-design.md](docs/superpowers/specs/2026-05-27-test-project-pytest-migration-design.md), PR-A section.

## Test plan
- [ ] CI's existing `lex test` invocations stay green
- [ ] `python -m lex pytest -m "not stress" --collect-only` collects the suite cleanly (no marker errors, no unconfigured-group errors)
- [ ] `find lex/test_project/tests -name 'test_*.py' -exec grep -L 'pytestmark = pytest.mark.' {} +` returns no files

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 2: Confirm CI is green on the PR**

CI must show the existing `lex test`-based gates passing (this PR does not change CI). Wait for completion; if any pre-existing job goes red, the failure is real (the marker lines should be inert) — investigate.

---

## PR-B — CI Flip PR

### Task B1: Update `copilot_pr_gate.yml` Mode A/C

**Files:**
- Modify: `.github/workflows/copilot_pr_gate.yml:195-219`

- [ ] **Step 1: Replace the Mode A/C run block**

In `.github/workflows/copilot_pr_gate.yml`, find the step starting at line 195:

```yaml
      - name: Mode A or C — run all new tests, expect pass
        if: steps.mode.outputs.mode != 'bug-repro'
        env:
```

Replace the entire `run:` block (lines 205-219) with:

```yaml
        run: |
          set -euo pipefail
          # `lex pytest` is the Lex CLI wrapper around pytest.main. The CLI
          # calls django.setup() in lex/bin/lex.py before pytest collection,
          # so Django ORM imports inside tests work without pytest-django.
          # Feed file paths (not dotted module names) to pytest.
          PATHS=""
          while IFS= read -r TEST_PATH; do
            [[ -z "$TEST_PATH" ]] && continue
            PATHS="$PATHS $TEST_PATH"
          done <<< "${{ steps.testfile.outputs.paths }}"
          echo "Running paths:$PATHS"
          python -m lex pytest $PATHS
```

Note the env block (`DJANGO_SETTINGS_MODULE`, `DATABASE_DEPLOYMENT_TARGET`, `CELERY_ACTIVE`) stays unchanged.

---

### Task B2: Update `copilot_pr_gate.yml` Mode B (with failure-detection regex)

**Files:**
- Modify: `.github/workflows/copilot_pr_gate.yml:221-271`

- [ ] **Step 1: Replace the Mode-B run block**

Find the step at line 221 (`Mode B — strip @expectedFailure, expect FAIL`). Replace its `run:` block (currently lines 227-271) with:

```yaml
        run: |
          set -euo pipefail
          # Single-file: locate step already errored out if >1 new test.
          TEST_PATH="${{ steps.testfile.outputs.first }}"
          # Strip the @(unittest.)?expectedFailure decorator IN PLACE.
          # The CI checkout is throwaway; overwriting keeps the file at
          # its real package path so the test runner discovers it as part
          # of lex/test_project/tests/<cluster>/.
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
          # Run via `lex pytest`. The lex CLI calls django.setup() before
          # pytest collection, so Django ORM is available.
          set +e
          python -m lex pytest "$TEST_PATH" -v 2>&1 | tee mode_b.out
          EXIT=${PIPESTATUS[0]}
          set -e
          if [[ $EXIT -eq 0 ]]; then
            echo "::error::Mode-B test passed without @expectedFailure — bug is not reproducible."
            exit 1
          fi
          # Distinguish "ran and asserted FAIL" from "errored on
          # import/setup". Pytest prints lines starting with `FAILED`,
          # `ERROR`, or a `==== FAILURES ====` separator for genuine test
          # failures. A collection or import error prints `ERRORS` (note
          # the plural separator) and exits non-zero too — we treat those
          # as non-reproductions because the test body never asserted.
          if grep -qE '^(FAILED |=+ FAILURES =+)' mode_b.out; then
            echo "Mode-B test correctly fails without the decorator."
          elif grep -qE '^(=+ ERRORS =+|ERROR )' mode_b.out; then
            echo "::error::Mode-B test errored before assertion (likely import/setup failure) — not a real bug repro."
            exit 1
          else
            echo "::error::Mode-B exit code non-zero but no recognised pytest FAILED/ERROR output — not a real bug repro."
            exit 1
          fi
```

- [ ] **Step 2: Commit**

```bash
cd /home/syscall/Documents/lex
git add .github/workflows/copilot_pr_gate.yml
git commit -m "Flip copilot_pr_gate.yml to lex pytest

Modes A/B/C now invoke python -m lex pytest with file paths instead of
lex test with dotted module labels. Mode-B failure-detection regex
updated to match pytest's FAILED / FAILURES / ERROR output formats.
The stale comment claiming pytest would crash without pytest-django is
removed — lex/bin/lex.py:117 calls django.setup() before any subcommand
so lex pytest is Django-aware.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task B3: Update `run_showcase_suite.py` — command construction

**Files:**
- Modify: `.github/scripts/run_showcase_suite.py:340-365`

- [ ] **Step 1: Locate and replace the `_run_cluster` cmd-build block**

In `.github/scripts/run_showcase_suite.py`, find the block currently at lines 353-363:

```python
    base_label = f"lex.test_project.tests.{cluster.key}"
    label = f"{base_label}.{test_suffix}" if test_suffix else base_label
    env = {**os.environ, **DJANGO_ENV}

    cmd = [
        "coverage", "run", "-a",
        "--rcfile=.coveragerc",
        "-m", "lex", "test",
        label,
        "--verbosity=2", "--noinput",
    ]
    if keepdb:
```

Replace with:

```python
    base_path = f"lex/test_project/tests/{cluster.key}"
    if test_suffix:
        # test_suffix arrives as a dotted Django label tail, e.g.
        # "test_1b_lex_init.TestCluster01b_LexInit.test_1_6b_init_runs_full_pipeline".
        # Translate to a pytest nodeid:
        #   "<module>.py::<Class>::<method>"
        parts = test_suffix.split(".")
        module = parts[0]
        rest = "::".join(parts[1:])
        target = f"{base_path}/{module}.py::{rest}" if rest else f"{base_path}/{module}.py"
    else:
        target = base_path
    env = {**os.environ, **DJANGO_ENV}

    cmd = [
        "coverage", "run", "-a",
        "--rcfile=.coveragerc",
        "-m", "lex", "pytest",
        target,
        "-v",
    ]
    if keepdb:
        # Django's --keepdb has no pytest equivalent for unittest.TestCase-based
        # tests. Django TestCase already uses per-test transaction rollback (no
        # DB recreation per test), so the speed characteristic is preserved.
        pass
```

Note the local variable rename from `label` to `target` and the renaming of `base_label` to `base_path`. The rest of the function (subprocess invocation, heartbeat printing) is unchanged.

- [ ] **Step 2: Verify the translation in isolation**

Add a temporary `__main__` test at the bottom of the file or use an inline Python REPL to verify:

```bash
cd /home/syscall/Documents/lex
python - <<'PY'
test_suffix = "test_1b_lex_init.TestCluster01b_LexInit.test_1_6b_init_runs_full_pipeline"
parts = test_suffix.split(".")
module = parts[0]
rest = "::".join(parts[1:])
expected = "test_1b_lex_init.py::TestCluster01b_LexInit::test_1_6b_init_runs_full_pipeline"
got = f"{module}.py::{rest}"
assert got == expected, f"mismatch: {got!r} != {expected!r}"
print("OK: dotted label translates to pytest nodeid correctly")

# Edge case: cluster-only (no test_suffix)
target = "lex/test_project/tests/init"
print(f"OK: cluster-only target = {target}")

# Edge case: module-only suffix (no Class/method)
parts2 = "test_1b_lex_init".split(".")
module2 = parts2[0]
rest2 = "::".join(parts2[1:])
got2 = f"lex/test_project/tests/init/{module2}.py::{rest2}" if rest2 else f"lex/test_project/tests/init/{module2}.py"
expected2 = "lex/test_project/tests/init/test_1b_lex_init.py"
assert got2 == expected2, f"module-only mismatch: {got2!r} != {expected2!r}"
print("OK: module-only suffix handled correctly")
PY
```

Expected: all three OK lines printed, no AssertionError.

---

### Task B4: Update `run_showcase_suite.py` — output parsing

**Files:**
- Modify: `.github/scripts/run_showcase_suite.py` (output-parsing functions; exact line numbers depend on which parsing helper exists)

- [ ] **Step 1: Locate the Django-runner output parsing code**

Run:

```bash
cd /home/syscall/Documents/lex
grep -n 'Ran [0-9]\|FAILED (failures=\|OK\|skipped\|expected failures' .github/scripts/run_showcase_suite.py
```

This will surface every place the script parses Django's `Ran N tests in T s` / `OK` / `FAILED (failures=N, errors=M, skipped=K, expected failures=X)` output.

- [ ] **Step 2: Replace each Django regex with the pytest equivalent**

Django format → Pytest format mapping (use these as the basis for `re.compile(...)` updates in the script):

| Django line | Pytest line(s) |
|---|---|
| `^Ran (\d+) tests? in ([\d.]+)s$` | `^=+ (?:(?P<passed>\d+) passed, ?)?(?:(?P<failed>\d+) failed, ?)?(?:(?P<errors>\d+) errors?, ?)?(?:(?P<skipped>\d+) skipped, ?)?(?:(?P<xfailed>\d+) xfailed, ?)?(?:(?P<xpassed>\d+) xpassed, ?)?.* in (?P<duration>[\d.]+)s =+$` |
| `^OK( \(.*\))?$` (success) | The pytest summary line above — success when `failed=0` and `errors=0` |
| `^FAILED \((?:failures=(\d+))?(?:, ?errors=(\d+))?(?:, ?skipped=(\d+))?(?:, ?expected failures=(\d+))?\)$` | Same pytest summary line — failure when `failed>0` or `errors>0` |

For each Django regex you found in Step 1, update it to match the pytest summary line and re-extract counts from named groups. The function's *external contract* (what it returns to the manifest writer) stays the same — only the parsing implementation changes.

Concretely: locate the function that parses `proc`'s stderr output (search for `re.compile` or `re.search` near the `Ran` / `OK` / `FAILED` strings). The likely signature is something like:

```python
def _parse_django_output(stderr: str) -> ClusterResult:
    # parses lines like:
    #   Ran 42 tests in 7.123s
    #   OK (expected failures=1, skipped=2)
    #   FAILED (failures=3, errors=1, skipped=2)
    ...
```

Rename it to `_parse_pytest_output` and replace the regexes with the pytest-summary-line parser:

```python
import re

_PYTEST_SUMMARY_RE = re.compile(
    r"^=+ "
    r"(?:(?P<failed>\d+) failed,? ?)?"
    r"(?:(?P<passed>\d+) passed,? ?)?"
    r"(?:(?P<skipped>\d+) skipped,? ?)?"
    r"(?:(?P<xfailed>\d+) xfailed,? ?)?"
    r"(?:(?P<xpassed>\d+) xpassed,? ?)?"
    r"(?:(?P<errors>\d+) errors?,? ?)?"
    r"(?:(?P<deselected>\d+) deselected,? ?)?"
    r"(?:(?P<warnings>\d+) warnings?,? ?)?"
    r"in (?P<duration>[\d.]+)s =+",
    re.MULTILINE,
)


def _parse_pytest_output(stderr: str) -> ClusterResult:
    """Parse pytest's terminal summary line into a ClusterResult.

    Pytest summary lines look like:
        ===== 42 passed in 7.12s =====
        ===== 3 failed, 39 passed, 2 skipped in 8.45s =====
        ===== 1 xfailed, 41 passed in 7.20s =====
        ===== 1 error in 0.45s =====
    The order of categories can vary between pytest versions; the regex
    above is order-agnostic for the categories pytest emits.
    """
    # Search the stderr (pytest writes its summary to stdout by default,
    # but lex pytest's plugin output and `-v` lines may go to either; the
    # call site already passes the combined buffer).
    last_match = None
    for m in _PYTEST_SUMMARY_RE.finditer(stderr):
        last_match = m
    if last_match is None:
        return ClusterResult(passed=0, failed=0, errors=0, skipped=0,
                             xfailed=0, xpassed=0, duration=0.0,
                             parse_error="no pytest summary line found")
    g = last_match.groupdict()
    return ClusterResult(
        passed=int(g["passed"] or 0),
        failed=int(g["failed"] or 0),
        errors=int(g["errors"] or 0),
        skipped=int(g["skipped"] or 0),
        xfailed=int(g["xfailed"] or 0),
        xpassed=int(g["xpassed"] or 0),
        duration=float(g["duration"]),
    )
```

Update the call site at the bottom of `_run_cluster` (or wherever the parser is invoked) to pass the combined stdout+stderr (pytest writes its summary to stdout, not stderr — verify by running one cluster manually in Step 3 and inspecting which stream the summary appears on).

Also update the `ClusterResult` dataclass (search for `ClusterResult` near the top of the file) — if it currently has fields like `failures`, `errors`, `skipped`, `expected_failures`, `tests_run`, add `xfailed`, `xpassed`, `passed` if missing. **Do NOT remove or rename existing fields** — the manifest writer and downstream coverage-summarisation code depend on the current shape; instead add the new pytest-native fields alongside, and derive any legacy ones (e.g. `tests_run = passed + failed + errors + skipped + xfailed + xpassed`) at the construction site.

- [ ] **Step 3: Smoke test the parser against real pytest output**

Run one cluster manually under pytest and inspect the output:

```bash
cd /home/syscall/Documents/lex
source ~/LUND_IT/ArmiraCashflowDB/.venv/bin/activate
DJANGO_SETTINGS_MODULE=lex_app.settings DATABASE_DEPLOYMENT_TARGET=default CELERY_ACTIVE=False \
  python -m lex pytest lex/test_project/tests/init -v 2>&1 | tail -15
```

Confirm the summary line at the bottom matches `_PYTEST_SUMMARY_RE` by running:

```bash
DJANGO_SETTINGS_MODULE=lex_app.settings DATABASE_DEPLOYMENT_TARGET=default CELERY_ACTIVE=False \
  python -m lex pytest lex/test_project/tests/init -v 2>&1 > /tmp/cluster_init.out

python - <<'PY'
import re, pathlib
text = pathlib.Path("/tmp/cluster_init.out").read_text()
rx = re.compile(
    r"^=+ "
    r"(?:(?P<failed>\d+) failed,? ?)?"
    r"(?:(?P<passed>\d+) passed,? ?)?"
    r"(?:(?P<skipped>\d+) skipped,? ?)?"
    r"(?:(?P<xfailed>\d+) xfailed,? ?)?"
    r"(?:(?P<xpassed>\d+) xpassed,? ?)?"
    r"(?:(?P<errors>\d+) errors?,? ?)?"
    r"(?:(?P<deselected>\d+) deselected,? ?)?"
    r"(?:(?P<warnings>\d+) warnings?,? ?)?"
    r"in (?P<duration>[\d.]+)s =+",
    re.MULTILINE,
)
matches = list(rx.finditer(text))
if not matches:
    print("FAIL: no summary line matched. Tail of output:")
    print("\n".join(text.splitlines()[-10:]))
    raise SystemExit(1)
last = matches[-1]
print("OK summary:", {k: v for k, v in last.groupdict().items() if v is not None})
PY
```

Expected: `OK summary: {...}` with non-None values for whichever categories appeared in the cluster's run (likely `passed`, `duration`, possibly `skipped` / `xfailed`).

If the regex misses, **stop and adjust** — the parser is the data feed for the showcase manifest; a silent miss will produce zero-count manifest entries.

- [ ] **Step 4: Commit**

```bash
cd /home/syscall/Documents/lex
git add .github/scripts/run_showcase_suite.py
git commit -m "Translate run_showcase_suite to lex pytest

_run_cluster: build pytest path/nodeid targets instead of dotted Django
labels; switch coverage run -m lex test → coverage run -m lex pytest.

Output parser: replace Django Ran/OK/FAILED line regexes with the
pytest summary-line regex (order-agnostic, handles passed/failed/
errors/skipped/xfailed/xpassed). Legacy ClusterResult fields preserved
for manifest-writer backward compatibility; new pytest-native fields
added alongside.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task B5: End-to-end verification before opening PR-B

- [ ] **Step 1: Test-count parity**

```bash
cd /home/syscall/Documents/lex
source ~/LUND_IT/ArmiraCashflowDB/.venv/bin/activate

# Baseline (Django runner, current production gate)
DJANGO_SETTINGS_MODULE=lex_app.settings DATABASE_DEPLOYMENT_TARGET=default CELERY_ACTIVE=False \
  lex test lex.test_project.tests --noinput 2>&1 | grep -E 'Ran [0-9]+ tests' | tee /tmp/django_count.txt

# New (pytest, post-flip)
DJANGO_SETTINGS_MODULE=lex_app.settings DATABASE_DEPLOYMENT_TARGET=default CELERY_ACTIVE=False \
  python -m lex pytest -m "not stress" --collect-only -q 2>&1 | tail -3 | tee /tmp/pytest_count.txt
```

Compare manually: the `Ran N` from Django should match the `N tests collected` from pytest within ±0. A divergence here means a test file is being discovered by one runner but not the other (likely a `test_*.py` missed by `pytestmark` insertion — re-run the Task A3 check).

- [ ] **Step 2: Showcase manifest parity**

Pick the smallest non-trivial cluster (e.g. `init` or `validation_hooks`) and run it through the showcase script to verify the manifest comes out shaped correctly:

```bash
cd /home/syscall/Documents/lex
source ~/LUND_IT/ArmiraCashflowDB/.venv/bin/activate
DJANGO_SETTINGS_MODULE=lex_app.settings DATABASE_DEPLOYMENT_TARGET=default CELERY_ACTIVE=False \
  python .github/scripts/run_showcase_suite.py --only init --out /tmp/manifest_pytest.json
cat /tmp/manifest_pytest.json | python -m json.tool | head -40
```

Expected: a JSON manifest with `init` cluster entry showing `passed`, `failed`, `errors`, `skipped`, `xfailed`, `duration` fields populated with sensible values (non-zero `passed`, zero `failed`/`errors` assuming the suite is green at HEAD).

If `passed=0` and `failed=0` and `errors=0`, the parser missed — back to Task B4 Step 3.

- [ ] **Step 3: Mode-B failure detection smoke**

This verifies PR-B's grep regex correctly distinguishes "test ran and asserted FAIL" from "test errored before assertion".

Create a deliberate failure scenario in a scratch worktree:

```bash
cd /home/syscall/Documents/lex
# Create a tiny test that will fail intentionally
cat > /tmp/test_repro.py <<'PY'
import unittest

class TestModeBSmoke(unittest.TestCase):
    def test_deliberate_failure(self):
        self.assertEqual(1, 2, "deliberate failure for Mode-B smoke")
PY

DJANGO_SETTINGS_MODULE=lex_app.settings DATABASE_DEPLOYMENT_TARGET=default CELERY_ACTIVE=False \
  python -m lex pytest /tmp/test_repro.py -v 2>&1 | tee /tmp/mode_b_smoke.out
set +e
grep -qE '^(FAILED |=+ FAILURES =+)' /tmp/mode_b_smoke.out && echo "REGEX MATCHES — Mode-B grep correctly identifies the failure"
grep -qE '^(=+ ERRORS =+|ERROR )' /tmp/mode_b_smoke.out && echo "WARNING: ERROR regex also matches — disambiguation needed"
set -e
```

Expected: the first echo prints `REGEX MATCHES — Mode-B grep correctly identifies the failure`. The second should NOT print. If the ERROR regex also matches, the failure path printed an ERROR-shaped line — adjust the grep ordering in `copilot_pr_gate.yml` Mode-B (check the FAILED regex first, then the ERROR regex, and treat ERROR as a non-reproduction).

- [ ] **Step 4: Coverage value within rounding**

Run the full suite under pytest with coverage and compare to the most recent green baseline:

```bash
cd /home/syscall/Documents/lex
source ~/LUND_IT/ArmiraCashflowDB/.venv/bin/activate
DJANGO_SETTINGS_MODULE=lex_app.settings DATABASE_DEPLOYMENT_TARGET=default CELERY_ACTIVE=False \
  coverage erase
DJANGO_SETTINGS_MODULE=lex_app.settings DATABASE_DEPLOYMENT_TARGET=default CELERY_ACTIVE=False \
  coverage run --rcfile=.coveragerc -m lex pytest -m "not stress"
coverage report --rcfile=.coveragerc | tail -5
```

Expected: the `TOTAL` line's coverage % matches the last known good `lex test`-based baseline within ±0.2pp. A larger drop means a code path is no longer being executed under pytest (most likely a fixture-lifecycle issue — see spec §7 R1).

---

### Task B6: Open PR-B

- [ ] **Step 1: Push and open**

```bash
cd /home/syscall/Documents/lex
git push -u origin HEAD
gh pr create --title "Test-project pytest migration — PR-B (CI flip)" --body "$(cat <<'EOF'
## Summary
- `copilot_pr_gate.yml` modes A/B/C now invoke `python -m lex pytest` (paths instead of dotted Django labels).
- `.github/scripts/run_showcase_suite.py` builds pytest nodeids and parses pytest's summary line.
- Mode-B failure-detection regex updated for pytest's `FAILED` / `FAILURES` output.
- Stale "pytest would crash on django.setup()" comment removed — `lex pytest` is Django-aware via `lex/bin/lex.py:117`.

Depends on PR-A. Spec: [docs/superpowers/specs/2026-05-27-test-project-pytest-migration-design.md](docs/superpowers/specs/2026-05-27-test-project-pytest-migration-design.md), PR-B section.

## Test plan
- [ ] `python -m lex pytest -m "not stress" --collect-only` reports same test count as previous `lex test` baseline
- [ ] Showcase manifest for a chosen cluster has non-zero `passed` and matches pre-flip baseline
- [ ] Mode-B smoke test: a deliberately-failing test triggers the `FAILED ` grep, not the ERROR grep
- [ ] Coverage report TOTAL within ±0.2pp of pre-flip baseline
- [ ] Three consecutive green CI runs on this branch

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 2: Wait for CI; verify three consecutive green runs**

If the first CI run is red, diagnose using Task B5's verification steps. Common causes:
- Marker name in YAML mismatches the folder name (Task A1 Step 2 should have caught this; if not, fix the YAML)
- A test file missed `pytestmark` (re-run the Task A3 Step 2 check)
- Parser regex misses a pytest version's summary format (Task B4 Step 3)

Re-run twice more after green to satisfy the test-plan's three-consecutive-greens rule.

---

## PR-C — Docs Update

### Task C1: Update `conventions.md`

**Files:**
- Modify: `lex/test_project/test-plan/progress/conventions.md`

- [ ] **Step 1: Replace the "How to Run Tests" section's code blocks**

Open `lex/test_project/test-plan/progress/conventions.md`. Find the section "How to Run Tests" (around line 109). Replace the three code blocks (`### Run all clusters`, `### Run a single cluster`, `### Run with coverage`) with this updated section:

```markdown
## How to Run Tests

### Run all clusters (excluding stress)
```bash
source ~/LUND_IT/ArmiraCashflowDB/.venv/bin/activate
python -m lex pytest -m "not stress"
```

### Run a single cluster
```bash
python -m lex pytest -m crud_api
```

### Run a single scenario by ID
```bash
python -m lex pytest -k "2_1"
```

### Run with coverage
```bash
coverage run -m lex pytest -m "not stress"
coverage report
```
```

(Pay attention: the inner code-fence indentation in the actual file uses backticks — preserve them as ``` exactly. The example above renders escape-correctly in this plan; in the file, the inner fences are ` ``` ` lines.)

- [ ] **Step 2: Add the additive sentence under "Naming Convention"**

Find the "Naming Convention" subsection. After the existing example code block (the one with `def test_02_01_create_sets_timestamps(self):`) — but **before** the "Assertion Messages" subsection — add this paragraph:

```markdown
Tests live in a `tests/<cluster_slug>/` folder. The folder's name is the
pytest group. At the top of each test module,
`pytestmark = pytest.mark.<cluster_slug>` declares the group once and
applies it to every test in the file.
```

- [ ] **Step 3: Add the additive sentence under "Test Class Organization"**

Find the "Test Class Organization" subsection (right after the example with `class TestCluster02_CRUDLifecycle(E2ETestCase):`). Add this sentence at the end of the subsection:

```markdown
Classes inherit from `E2ETestCase` as before; no per-class `@pytest.mark`
decoration is needed because the module-level `pytestmark` already applies
to every test in the file.
```

- [ ] **Step 4: Add the additive sentence under "Test First, Then Fix"**

Find the "Test First, Then Fix" rule (around line 33). After step 5 ("When the bug is fixed, remove `@unittest.expectedFailure` — the test should now pass naturally"), add this paragraph:

```markdown
`@pytest.mark.xfail(strict=True)` is an acceptable equivalent for tests
authored after the pytest cutover, but existing `@unittest.expectedFailure`
markers are not bulk-converted.
```

- [ ] **Step 5: Verify the rules sections are byte-identical**

Use a checksum to prove the Quality Gates and the canonical rule wording are not modified:

```bash
cd /home/syscall/Documents/lex
# Capture the Quality Gates section text and hash it.
python - <<'PY'
import hashlib, pathlib, re
text = pathlib.Path("lex/test_project/test-plan/progress/conventions.md").read_text()
# Quality Gates section: from "## Quality Gates" to the next "##" or end.
m = re.search(r"## Quality Gates\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
if not m:
    print("FAIL: Quality Gates section not found")
    raise SystemExit(1)
section = m.group(1).strip()
h = hashlib.sha256(section.encode()).hexdigest()
print(f"Quality Gates section sha256: {h}")
# Compare against the pre-PR-C value below — record it on the first run,
# then re-run after edits to confirm no drift.
PY
```

Record the hash before edits, run after edits, confirm they match. If they differ, the edits accidentally touched the rules section — revert and try again.

- [ ] **Step 6: Commit**

```bash
cd /home/syscall/Documents/lex
git add lex/test_project/test-plan/progress/conventions.md
git commit -m "conventions.md: pytest runner examples; rules unchanged

- 'How to Run Tests' code blocks now show python -m lex pytest invocations.
- Added one sentence each under Naming Convention, Test Class Organization,
  and Test First Then Fix explaining the marker pattern and xfail equivalence.
- Quality Gates, Golden Rule, and the rest of the rules sections are
  byte-identical to the pre-PR version.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task C2: Update `index.md`

**Files:**
- Modify: `lex/test_project/test-plan/index.md`

- [ ] **Step 1: Add the additive sentence under "Test Project Structure"**

Open `lex/test_project/test-plan/index.md`. Find the section "Test Project Structure" (around line 53). After the closing triple-backticks of the directory-tree code block (around line 95, immediately before the "Naming convention:" line), add:

```markdown
Each cluster folder name is also its pytest group. The marker is declared
once per test module via `pytestmark = pytest.mark.<group>`; the canonical
group list lives in `lex_test_config.yaml` at the repo root.
```

- [ ] **Step 2: Verify naming-convention regex line is unchanged**

```bash
cd /home/syscall/Documents/lex
grep -n 'test_<Nx>_<slug>.py' lex/test_project/test-plan/index.md
```

Expected: exactly one line, byte-identical to the pre-edit version. If the line is different, revert the change and try again.

- [ ] **Step 3: Commit**

```bash
cd /home/syscall/Documents/lex
git add lex/test_project/test-plan/index.md
git commit -m "index.md: note that folder name = pytest group

Single additive sentence under Test Project Structure. Folder tree,
naming convention regex, cluster table, and the Golden Rule call-out
are byte-identical.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task C3: Update `test-writing-plan.md`

**Files:**
- Modify: `lex/test_project/test-plan/test-writing-plan.md`

- [ ] **Step 1: Add the footnote at the bottom**

Open `lex/test_project/test-plan/test-writing-plan.md`. After the existing "## 9. Rules every batch must follow" section's last item (rule 7, the bare-except ban around line 496), and before the trailing blank lines, add:

```markdown

---

> **Runner note (May 2026):** this suite runs under `python -m lex pytest`.
> New batches add `pytestmark = pytest.mark.<cluster_slug>` to each test
> module. See [`progress/conventions.md` §How to Run Tests](progress/conventions.md#how-to-run-tests)
> for the runner commands.
```

- [ ] **Step 2: Verify the 7 rules are byte-identical**

```bash
cd /home/syscall/Documents/lex
python - <<'PY'
import hashlib, pathlib, re
text = pathlib.Path("lex/test_project/test-plan/test-writing-plan.md").read_text()
m = re.search(r"## 9\. Rules every batch must follow\n(.*?)(?=\n## |\n---\n>|\Z)", text, re.DOTALL)
if not m:
    print("FAIL: Rules section not found")
    raise SystemExit(1)
rules = m.group(1).strip()
print(f"§9 rules sha256: {hashlib.sha256(rules.encode()).hexdigest()}")
print("First 200 chars:", rules[:200])
PY
```

Compare hash before/after edits.

- [ ] **Step 3: Commit**

```bash
cd /home/syscall/Documents/lex
git add lex/test_project/test-plan/test-writing-plan.md
git commit -m "test-writing-plan.md: runner footnote at bottom

Single additive blockquote footnote after §9 Rules pointing to the new
runner command. §9's 7 rules are byte-identical to the pre-PR version.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task C4: Verify `test-clusters.md` Testing Philosophy is untouched

**Files:**
- Read-only check: `lex/test_project/test-plan/test-clusters.md`

- [ ] **Step 1: Confirm no edits crept in**

```bash
cd /home/syscall/Documents/lex
git diff origin/main -- lex/test_project/test-plan/test-clusters.md
```

Expected: **empty diff**. If anything appears, revert it (`git checkout origin/main -- lex/test_project/test-plan/test-clusters.md`). The Testing Philosophy section is the Golden Rule home and must remain byte-identical.

---

### Task C5: Update the single `CLAUDE.md` reference

**Files:**
- Modify: `CLAUDE.md` (single line — confirmed at line 358 in the spec)

- [ ] **Step 1: Locate the test_project-specific invocation**

```bash
cd /home/syscall/Documents/lex
grep -n 'lex test.*test_project\|lex.test_project.tests' CLAUDE.md
```

The only line that should match is the one invoking `lex test` against `lex.test_project.tests` (e.g. as part of a full-suite invocation alongside the framework trees).

- [ ] **Step 2: Replace ONLY the test_project portion**

Lines that combine framework trees and test_project, like:

```bash
lex test lex.core.tests lex.audit_logging.tests lex.process_admin.tests lex.tests lex.test_project.tests
```

stay mostly intact, but the `lex.test_project.tests` argument no longer fits the `lex test` argument list — pytest takes a path, not a dotted label. Split the line into two invocations:

```bash
# Framework-side suites (deprecated, kept running for now)
lex test lex.core.tests lex.audit_logging.tests lex.process_admin.tests lex.tests
# test_project suite (migrated to pytest)
python -m lex pytest -m "not stress"
```

Apply the same split to any other line in `CLAUDE.md` that bundles `lex.test_project.tests` together with framework trees. Lines that reference *only* the framework trees (e.g. `lex.core.tests.test_bitemporal` alone) stay verbatim — they are out of scope per spec D4.

- [ ] **Step 3: Commit**

```bash
cd /home/syscall/Documents/lex
git add CLAUDE.md
git commit -m "CLAUDE.md: split test_project invocations off the lex test line

Lines that previously bundled lex.test_project.tests into a lex test
command now show two invocations: one for the deprecated framework
trees (lex test) and one for test_project (python -m lex pytest).
Framework-tree-only lines are untouched per migration scope (D4).

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task C6: Open PR-C

- [ ] **Step 1: Push and open**

```bash
cd /home/syscall/Documents/lex
git push -u origin HEAD
gh pr create --title "Test-project pytest migration — PR-C (docs)" --body "$(cat <<'EOF'
## Summary
- `conventions.md`, `index.md`, `test-writing-plan.md`, `CLAUDE.md` updated to reference `python -m lex pytest`.
- All Golden Rule / Quality Gates / §9 rules sections are byte-identical to the pre-PR version (verified by sha256 checksums during implementation).

Depends on PR-A and PR-B. Spec: [docs/superpowers/specs/2026-05-27-test-project-pytest-migration-design.md](docs/superpowers/specs/2026-05-27-test-project-pytest-migration-design.md), PR-C section.

## Test plan
- [ ] Every code block in `conventions.md` is copy-pasteable into a shell and works
- [ ] `git diff origin/main -- lex/test_project/test-plan/test-clusters.md` is empty
- [ ] Quality Gates section sha256 matches pre-edit
- [ ] §9 7-rules sha256 matches pre-edit
- [ ] A new contributor can write a test in a new sub-cluster using only the doc

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## End-to-end acceptance (after all three PRs merge)

- [ ] `lex_test_config.yaml` exists at repo root and `python -m lex pytest --collect-only` runs without error
- [ ] Every test file under `lex/test_project/tests/<group>/` carries `pytestmark = pytest.mark.<group>`
- [ ] `copilot_pr_gate.yml` modes A/B/C invoke `python -m lex pytest`
- [ ] `.github/scripts/run_showcase_suite.py` builds pytest-style paths and parses pytest's output
- [ ] Three consecutive green CI runs on the flipped workflows
- [ ] Coverage value tracks within ±0.2pp of the last `lex test` baseline
- [ ] `conventions.md`, `index.md`, `test-writing-plan.md` examples reference `python -m lex pytest`; rules sections are byte-identical
- [ ] Reverting PR-B alone restores `lex test`-based CI while PR-A's marker lines sit harmlessly in place
