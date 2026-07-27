# Test-Project Migration to `pytest` + `lex_test_config.yaml` — Design

**Status:** Draft, pending user review
**Date:** 2026-05-27
**Scope owner:** Testing infrastructure
**Goal:** Migrate `lex/test_project/tests/` from `lex test` (Django test runner) to `python -m lex pytest` + `lex_test_config.yaml`, without changing any rule, philosophy, or scenario in the test plan.

---

## 1. Goal and non-goals

### 1.1 Goal

Adopt the new testing structure already implemented in `lex/tools/test_groups.py`:

- A single `lex_test_config.yaml` at the repo root declares the `tests_entrypoint`, the logical `groups`, and (for future use) `receivers` / `report` / `email` blocks.
- Tests are assigned to a group via `@pytest.mark.<group>` — applied once per test module via `pytestmark`.
- CI runs the suite via `python -m lex pytest` (which calls `_pytest.main` in-process, with the plugin registered).

The rules of the test plan (Golden Rule, Given/When/Then, scenario numbering, expectedFailure-with-tracker, cluster ordering, coverage-only-goes-up, no `skip_hooks`, no mocking the ORM, etc.) are **preserved verbatim**. Only notation and runner change.

### 1.2 Non-goals (explicit)

- PDF report generation and email delivery (`--report`, `--report-and-email`) — wired but not turned on
- Populating `receivers:` in the YAML
- Bulk conversion of `@unittest.expectedFailure` → `@pytest.mark.xfail`
- Conversion of test classes to function-based pytest
- Sub-cluster-level markers (we use cluster-level + `-k` for sub-cluster selection)
- Wiring `pytest`'s per-group aggregation into the dashboard automation
- Migration of the deprecated framework-side test trees (`lex/tests/unit/`, `lex/tests/integration/`, `lex/tests/e2e/`, `lex/core/tests/`, `lex/audit_logging/tests/`, `lex/process_admin/tests/`, `lex/lex_app/tests/`) — the user has confirmed these are deprecated and out of scope

---

## 2. Decisions locked

| # | Decision | Source |
|---|---|---|
| D1 | Scope is **notation only** — no PDF/email work, no recipient lists | User Q1 = A |
| D2 | **Minimal-touch**: unittest.TestCase subclasses, `setUp`, `self.client`, `self.assertX`, `@unittest.expectedFailure`, `@unittest.skip` all stay verbatim | User Q2 = C |
| D3 | **Cluster-level markers** + `-k` for sub-cluster selection | User Q3 = C |
| D4 | Scope = `lex/test_project/tests/` only; other test trees deprecated | User Q4 explicit |
| D5 | **Two-PR feature-flag cutover**: PR-A (inert prep), PR-B (CI flip) | User Q5 = C |
| D6 | Group set = directory names, **single-marker rule**, `stress` opt-in via `-m "not stress"` | User Q6 implicit accept |
| D7 | `lex_test_config.yaml` at repo root | `test_groups.py:200` loader expects `project_root / CONFIG_FILENAME` |
| D8 | `tests_entrypoint: lex/test_project/tests` | Matches D4 |
| D9 | `@unittest.expectedFailure` and `@unittest.skip` kept verbatim — pytest respects both natively | Minimal-touch principle |
| D10 | No new `conftest.py` added; `lex/bin/lex.py:117` already calls `django.setup()` before any subcommand | Verified in source |
| D11 | `report:`, `email:`, `receivers:` present in YAML with safe defaults so schema validates and future opt-in is a config edit | `test_groups.py` loader rejects missing keys |
| D12 | `asgi_responsiveness/` is **not** included as a group in the initial YAML (no `test_*.py` exists yet); added later when tests land | Verified in directory listing |

---

## 3. The `lex_test_config.yaml` file

New file at `/home/syscall/Documents/lex/lex_test_config.yaml` (repo root):

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

16 groups. `asgi_responsiveness` is intentionally omitted; it will be added by the PR that ships the first test file in that folder.

---

## 4. PR-A — Prep PR (marker + YAML, inert under `lex test`)

### 4.1 What changes

- New file: `lex_test_config.yaml` at repo root (Section 3)
- Modified file: every `lex/test_project/tests/<group>/test_*.py` gets two lines added at the top of the imports block:

```python
import pytest

pytestmark = pytest.mark.<group>
```

where `<group>` is the parent folder name.

`stress/` test files (when they exist) get `pytestmark = pytest.mark.stress`.

### 4.2 What does NOT change

- `lex/test_project/tests/<group>/models.py`
- `lex/test_project/tests/<group>/__init__.py` (packages cannot carry `pytestmark`)
- `lex/test_project/tests/fixtures/`
- `E2ETestCase` and base hierarchy
- Test class names, method names, docstrings, assertions
- `.github/workflows/*.yml`
- `.github/scripts/run_showcase_suite.py`
- Any test-plan documentation (handled in PR-C)
- `CLAUDE.md`

### 4.3 Why it's inert under `lex test`

Django's test runner ignores `pytestmark` (it's just a module-level attribute holding a marker object). The added `import pytest` line has no side effects. The existing `lex test lex.test_project.tests --noinput` continues to pass identically.

### 4.4 Verification gates for PR-A

Before merging:

1. **Existing runner unchanged:** `lex test lex.test_project.tests --noinput` and the showcase workflow continue to pass with their pre-PR test counts and pre-PR coverage.
2. **YAML loads cleanly:** `python -c "from lex.tools.test_groups import resolve_config, load_config_from_disk; from pathlib import Path; print(load_config_from_disk(Path('.')))"` runs without raising `LexTestConfigError`.
3. **Pytest collects everything:** `python -m lex pytest -m "not stress" --collect-only` reports the same test count as `lex test` does (within ±0; pytest and Django discover unittest classes identically). This step is added as a **non-gating CI advisory** so we see any divergence before PR-B flips the gate.
4. **No missing markers:** the following one-liner returns no files, run from the repo root:

   ```bash
   find lex/test_project/tests -name 'test_*.py' \
     -exec grep -L 'pytestmark = pytest.mark.' {} +
   ```

   (added as a CI step in the PR-A advisory job)
5. **Every used marker is in the YAML:** the test_groups.py plugin enforces this at collection — any unconfigured marker hard-fails `lex pytest`. Verified by gate 3.

---

## 5. PR-B — Flip PR (CI runner swap)

### 5.1 Scope

Two CI surfaces invoke `lex test` on `lex/test_project/`:

| File | Role | Today's invocation |
|---|---|---|
| `.github/workflows/copilot_pr_gate.yml` | Per-PR — runs **only the new test files added by the PR** (modes A/B/C) | `lex test $MODULES --noinput` where `$MODULES` is a space-separated list of dotted module paths derived from changed `.py` files |
| `.github/scripts/run_showcase_suite.py` (called by `.github/workflows/showcase_tests.yml`) | Full-suite — runs each cluster as a separate process under `coverage run -a` | `coverage run -a --rcfile=.coveragerc -m lex test <label> --verbosity=2 --noinput` where `<label>` is `lex.test_project.tests.<cluster>[.<test_suffix>]` |

Both get translated to `lex pytest` invocations. Other workflows (`copilot_coverage_check.yml`, `copilot_publish_after_merge.yml`, `copilot_test_bot.yml`, `pip_publish.yml`, etc.) do **not** invoke `lex test` against `test_project/` and require no change.

### 5.2 Change A — `copilot_pr_gate.yml`

The current "Mode A or C" step (lines ~195-219) builds dotted module paths from changed `.py` paths and calls `lex test $MODULES --noinput`. The replacement keeps the path-list approach but feeds **paths** (not dotted modules) to `lex pytest`:

```yaml
- name: Mode A or C — run all new tests, expect pass
  if: steps.mode.outputs.mode != 'bug-repro'
  env:
    DJANGO_SETTINGS_MODULE: lex_app.settings
    DATABASE_DEPLOYMENT_TARGET: default
    CELERY_ACTIVE: "False"
  run: |
    set -euo pipefail
    # `lex pytest` is the Lex CLI wrapper around pytest.main. The CLI
    # calls django.setup() before pytest collection, so Django ORM
    # imports inside tests work without pytest-django.
    PATHS=""
    while IFS= read -r TEST_PATH; do
      [[ -z "$TEST_PATH" ]] && continue
      PATHS="$PATHS $TEST_PATH"
    done <<< "${{ steps.testfile.outputs.paths }}"
    echo "Running paths:$PATHS"
    python -m lex pytest $PATHS
```

The "Mode B" step (lines ~221-269) strips `@expectedFailure` and re-runs the single test, expecting failure. Same translation: `lex test "$MODULE"` becomes `python -m lex pytest "$TEST_PATH"`.

The stale CI comment at `copilot_pr_gate.yml:248-252` warning that "pytest without pytest-django would crash on django.setup()" is **deleted in PR-B**. It described invoking pytest directly, not via `lex pytest`; `lex/bin/lex.py:117` calls `django.setup()` before any subcommand, so `lex pytest` is Django-aware. Leaving the comment would actively mislead future readers.

The failure-detection grep (`grep -qE '^(FAIL|FAILED)' mode_b.out`) is updated to match pytest's failure output format:

```bash
if grep -qE '^(FAILED|ERROR|=+ FAILURES =+)' mode_b.out; then
  echo "Mode-B test correctly fails without the decorator."
else
  echo "::error::Mode-B test errored before assertion (likely import/setup failure) — not a real bug repro."
  exit 1
fi
```

### 5.3 Change B — `.github/scripts/run_showcase_suite.py`

The `_run_cluster` function (around lines 340-365) constructs:

```python
base_label = f"lex.test_project.tests.{cluster.key}"
label = f"{base_label}.{test_suffix}" if test_suffix else base_label
cmd = [
    "coverage", "run", "-a",
    "--rcfile=.coveragerc",
    "-m", "lex", "test",
    label,
    "--verbosity=2", "--noinput",
]
```

Translation:

```python
base_path = f"lex/test_project/tests/{cluster.key}"
if test_suffix:
    # test_suffix is "module.Class.method" — translate to pytest nodeid:
    # "module.Class.method" → "module.py::Class::method"
    parts = test_suffix.split(".")
    module = parts[0]
    rest = "::".join(parts[1:])
    target = f"{base_path}/{module}.py::{rest}" if rest else f"{base_path}/{module}.py"
else:
    target = base_path
cmd = [
    "coverage", "run", "-a",
    "--rcfile=.coveragerc",
    "-m", "lex", "pytest",
    target,
    "-v",
]
if keepdb:
    # pytest equivalent of Django's --keepdb is not natively supported;
    # since tests inherit from Django's TestCase, the per-test transaction
    # rollback already provides the same speed-up. Drop the keepdb flag.
    pass
```

The stderr-parsing code that currently looks for Django's `FAIL:` / `OK` / `Ran N tests` lines (search for those strings elsewhere in the script) needs equivalent regex updates for pytest's output. Identified during PR-B implementation:
- `Ran <N> tests in <T>s` → `==== <N> passed in <T>s ====` (and variants with `failed`, `error`, `skipped`, `xfailed`, `xpassed`)
- `FAILED (failures=N)` / `FAILED (errors=N)` → `==== <N> failed`, `<N> error`
- `OK` → `==== <N> passed`

### 5.4 What does NOT change in PR-B

- `COVERAGE_FAIL_UNDER` value — the test plan's "threshold only goes up" rule is preserved
- `pip_publish.yml`'s Redis broker workflow (cluster 8k) — uses its own invocation pattern, not `lex test` against the suite
- The cluster list / showcase selector in `showcase_clusters.py` — clusters keep their keys
- Coverage tooling (`.coveragerc`) — unchanged
- DB-setup behavior — `lex pytest` discovers `unittest.TestCase` subclasses, which still use Django's per-test-transaction rollback via `setUpClass`/`tearDownClass`

### 5.5 Verification gates for PR-B

1. **Same test count:** `python -m lex pytest lex/test_project/tests -m "not stress" --collect-only -q | tail -1` reports the same total as `lex test lex.test_project.tests --noinput -v 2 2>&1 | grep "test in"` (within rounding for skipped/xfail categorization).
2. **Showcase manifest matches:** `python .github/scripts/run_showcase_suite.py --only "<existing-selector>" --out manifest.json` produces a manifest with the same per-cluster pass/fail/skip/xfail counts as the pre-flip baseline.
3. **Three consecutive green runs** of the flipped `copilot_pr_gate.yml` on a representative PR (matches `conventions.md` §Quality Gates rule 3).
4. **Mode-B failure detection works:** a deliberately-failing test (without `@expectedFailure`) under Mode-B is detected via the updated regex, not silently passed through.
5. **Coverage value within rounding:** `coverage report` after `python -m lex pytest` matches the last `lex test` value within ±0.2pp.
6. **Revert is one-PR:** `git revert <PR-B-commit>` restores both files to their previous state.

---

## 6. PR-C — Documentation update (doc-only)

### 6.1 Files updated and what changes

**`lex/test_project/test-plan/progress/conventions.md`**

§"How to Run Tests" — three code blocks change to:

```bash
# Run all clusters (excluding stress)
source ~/LUND_IT/ArmiraCashflowDB/.venv/bin/activate
python -m lex pytest -m "not stress"

# Run a single cluster
python -m lex pytest -m crud_api

# Run a single scenario by ID
python -m lex pytest -k "2_1"

# Run with coverage
coverage run -m lex pytest -m "not stress"
coverage report
```

§"Naming Convention" — example block stays structurally identical. One sentence added below: *"Tests live in a `tests/<cluster_slug>/` folder. The folder's name is the pytest group. At the top of each test module, `pytestmark = pytest.mark.<cluster_slug>` declares the group once and applies it to every test in the file."*

§"Test Class Organization" — unchanged. One-line note added: *"Classes inherit from `E2ETestCase` as before; no per-class `@pytest.mark` decoration is needed because the module-level `pytestmark` already applies to every test in the file."*

§"Quality Gates" — **unchanged**. All six gates keep their wording.

§"Test First, Then Fix" — **unchanged**. `@unittest.expectedFailure` remains the canonical marker. Trailing sentence added: *"`@pytest.mark.xfail(strict=True)` is an acceptable equivalent for tests authored after the pytest cutover, but existing `@unittest.expectedFailure` markers are not bulk-converted."*

**`lex/test_project/test-plan/index.md`**

§"Test Project Structure" — folder tree at the top is **unchanged**. One sentence added below it: *"Each cluster folder name is also its pytest group. The marker is declared once per test module via `pytestmark = pytest.mark.<group>`; the canonical group list lives in `lex_test_config.yaml` at the repo root."*

Naming-convention regex (`test_<Nx>_<slug>.py`) — **unchanged**.

**`lex/test_project/test-plan/test-writing-plan.md`**

§"Conventions for this plan" — **unchanged**.
§9 "Rules every batch must follow" — **all 7 rules unchanged verbatim**.
Footnote added at the bottom: *"Effective May 2026, this suite runs under `python -m lex pytest`. New batches add `pytestmark = pytest.mark.<cluster_slug>` to each test module. See `progress/conventions.md` §How to Run Tests for the runner commands."*

**`lex/test_project/test-plan/test-clusters.md`**

§"Testing Philosophy" — **verified unchanged before commit** (this is the Golden Rule home).
Per-cluster sections — **unchanged**.

**`CLAUDE.md`**

Any line documenting `lex test lex.test_project.tests*` gets the same one-line swap. Lines referencing the framework-side test trees (`lex.tests.unit`, `lex.core.tests`, etc.) are left alone — they are deprecated and out of scope.

### 6.2 What does NOT change

- Golden Rule (test intent, not implementation)
- Given / When / Then docstring template
- Scenario numbering (`X.Y`)
- "One Cluster at a Time" rule
- Known Bugs Tracker process
- `cluster slug` ↔ folder name ↔ marker name (all the same string)
- The 7 rules in test-writing-plan.md §9
- The 6 Quality Gates in conventions.md

### 6.3 Acceptance for PR-C

- A new contributor reading `conventions.md` end-to-end can write a passing test in a new sub-cluster using only the doc.
- Every code block in `conventions.md` is copy-pasteable into a shell and works.
- The Golden Rule line, the seven §9 rules, and the six Quality Gates `grep` to byte-identical strings before and after.

### 6.4 PR ordering

Recommended order: **A → B → C**. PR-C describes the runner that is currently gating CI, so landing it after PR-B avoids documenting an aspirational state.

---

## 7. Risks and mitigations

| # | Risk | Likelihood | Mitigation |
|---|---|---|---|
| R1 | Fixture-lifecycle differences between Django's runner and pytest's `unittest.TestCase` adapter (e.g. `setUpTestData` ordering, transactional rollback boundaries) cause a test to pass under one and fail under the other. | Medium | PR-A's verification gate 3 runs `lex pytest --collect-only` as a non-gating advisory step, so any divergence shows up in the prep PR's CI without blocking it. Real-execution divergence is caught by PR-B verification gate 1 (same test count) and gate 2 (showcase manifest match). |
| R2 | The `test_groups.py` plugin hard-fails on unconfigured markers; a stray `@pytest.mark.something` breaks collection. | Low | Single-marker rule (D6) plus complete 16-group YAML means no legitimate need for an extra marker. Plugin error names the missing group; fix is one YAML line. |
| R3 | A test file under `tests/<group>/` accidentally misses the `pytestmark` line; tests run but don't count toward any group's totals. | Low | PR-A verification gate 4 (no missing `pytestmark`) catches this at PR time. |
| R4 | Dashboard's per-cluster counts (`dashboard.md`) currently come from manual session-log accounting, not from the runner. Out of scope for now but worth noting. | None for this migration | Dashboard arithmetic continues as before. A separate future PR can wire `pytest`'s group counts into automation. |
| R5 | `@unittest.expectedFailure` under pytest reports as `XFAIL` (matching dashboard column). If an `expectedFailure`-marked test unexpectedly passes, pytest reports `XPASS`. | Low | Pytest's default `XPASS` is non-strict (warning, not failure) — matches unittest's behavior. Documented in PR-C addendum. |
| R6 | `pip_publish.yml` runs the opt-in Redis broker test (cluster 8k) as a release gate. | None | Verified — cluster 8k uses its own redis-broker workflow pattern, not `lex test`. No change needed in PR-B. |
| R7 | Minimal-touch decision leaves the suite unittest-flavoured indefinitely; new contributors who only know pytest may write inconsistent style. | Low | PR-C documents that both styles are accepted. Opportunistic rewrite later (D2) covers long-tail drift. |
| R8 | Mode-B's failure-detection regex in `copilot_pr_gate.yml` is tightly coupled to Django runner output (`FAIL:` / `FAILED`); pytest's output format is different. | High if missed; low if planned | PR-B explicitly updates the regex (Section 5.2). PR-B verification gate 4 verifies it works against a deliberately-failing repro. |
| R9 | `run_showcase_suite.py` parses Django runner output (`Ran N tests in T s`, `OK`, `FAILED (failures=N)`) for the manifest. Pytest output is different. | High if missed | PR-B Section 5.3 calls out the output-parsing update as part of the script change. PR-B verification gate 2 (manifest match) catches it. |
| R10 | `--keepdb` flag on Django's runner has no pytest equivalent for unittest.TestCase-based tests; speed regression possible. | Low | Django `TestCase` already uses per-test transaction rollback (no DB recreation per test) — same speed under pytest. PR-B Section 5.3 documents this. |
| R11 | A test currently relies on Django runner-specific behavior (e.g. test discovery order, `_pre_setup`/`_post_teardown` ordering peculiarities). | Low | Pytest's unittest adapter is documented as a drop-in for `TestCase` discovery. If found, the test is fixed at its own boundary (use `setUpClass` properly) rather than reverting the migration. |

---

## 8. Edge cases

- **`tests/__init__.py`** and **`tests/<group>/__init__.py`** are package files, not test modules. No `pytestmark` goes there.
- **Files with multiple test classes** all collect under the same module-level `pytestmark`. One marker covers them all.
- **`tests/fixtures/`** contains no tests. `lex pytest -m "not stress"` will not collect anything from it.
- **`tests/asgi_responsiveness/`** has only `__init__.py` + `models.py` today (no `test_*.py`). Omitted from the initial YAML; the PR that adds the first test file in that folder also adds the `asgi_responsiveness` group to the YAML.
- **Conditional skips** (env-gated 8k Redis tests, auto-skip MetaHistorical tests in 5k) use `@unittest.skipUnless(...)` / `self.skipTest(...)`. Pytest respects both.
- **Mode-B's @expectedFailure-stripping regex** at `copilot_pr_gate.yml:241` (`^\s*@(?:unittest\.)?expectedFailure\s*\n`) already handles both forms; no change needed there.

---

## 9. Acceptance — end-to-end

The migration is complete when all of the following hold:

1. `lex_test_config.yaml` exists at repo root and `python -m lex pytest --collect-only` runs without error.
2. Every test file under `lex/test_project/tests/<group>/` carries a module-level `pytestmark = pytest.mark.<group>` line.
3. `copilot_pr_gate.yml` modes A/B/C invoke `python -m lex pytest` and the Mode-B failure-detection regex matches pytest's output format.
4. `.github/scripts/run_showcase_suite.py` builds pytest-style paths (`a/b/c.py::Class::method`) and parses pytest's output for the manifest.
5. Three consecutive green CI runs on the flipped workflows.
6. Coverage value tracks within ±0.2pp of the last `lex test` baseline.
7. `conventions.md`, `index.md`, `test-writing-plan.md` examples reference `python -m lex pytest`; rules sections are byte-identical to the pre-migration version.
8. Revert plan documented and tested: reverting PR-B alone restores `lex test`-based CI while leaving PR-A's marker lines harmlessly in place.

---

## 10. Open items resolved at spec time

Already resolved during this design:

- **Exact current invocation in `copilot_pr_gate.yml`** — confirmed at lines 219, 254 (`lex test $MODULES --noinput` and `lex test "$MODULE" --noinput`)
- **Current coverage workflow** — confirmed: `run_showcase_suite.py:357-363` uses `coverage run -a --rcfile=.coveragerc -m lex test <label> --verbosity=2 --noinput`
- **`CLAUDE.md` `lex test` references** — confirmed present at lines 90, 179, 358, 362, 365, 443, 464, 468 (some refer to the deprecated framework trees and stay; the `lex.test_project` mention at 358 gets swapped)
- **`test-clusters.md` Testing Philosophy section** — to be verified byte-identical at PR-C diff time
- **`django.setup()` wiring for `lex pytest`** — confirmed at `lex/bin/lex.py:117`

No remaining unknowns block plan-writing.
